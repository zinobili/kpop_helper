import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol

from . import config, glossary
from .rate_limiter import RateLimiter
from .vtt_utils import Cue

logger = logging.getLogger("kpop_helper.translate")

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_LANG_NAMES = {"ko": "Korean", "en": "English"}

# Shared across all jobs/threads: the RPM cap is per Gemini API key/project, not per video.
_gemini_limiter = RateLimiter(config.GEMINI_RPM, period_seconds=60.0)

BatchProgressCallback = Callable[[int, int], None]


class BatchCheckpoint(Protocol):
    """Where finished batches are saved as they complete, so a job that fails partway can resume
    instead of re-paying for them. cache.BatchCheckpoint is the real implementation."""

    def load(self, prompt_hash: str) -> Optional[List[str]]: ...

    def save(self, prompt_hash: str, translations: List[str]) -> None: ...

# Provider functions report token usage / stop reason here (per thread, since each job runs in its
# own thread) rather than via their return value, so their signatures stay List[str]-returning.
_call_info = threading.local()

_TRUNCATION_STOP_REASONS = ("max_tokens", "length")


def _record_call_info(
    model=None, input_tokens=None, output_tokens=None, thinking_tokens=None, stop_reason=None
) -> None:
    _call_info.value = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "stop_reason": None if stop_reason is None else str(stop_reason),
    }


def _take_call_info() -> dict:
    info = getattr(_call_info, "value", None) or {}
    _call_info.value = None
    return info


def _is_truncated(info: dict) -> bool:
    stop = (info.get("stop_reason") or "").lower()
    return any(marker in stop for marker in _TRUNCATION_STOP_REASONS)


def _format_call_info(info: dict) -> str:
    return (
        f"model={info.get('model')} in_tok={info.get('input_tokens')} "
        f"out_tok={info.get('output_tokens')} think_tok={info.get('thinking_tokens')} "
        f"stop={info.get('stop_reason')}"
    )


def _openai_compat_call_info(data: dict, model: str) -> None:
    usage = data.get("usage") or {}
    choices = data.get("choices") or [{}]
    _record_call_info(
        model=model,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        thinking_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        stop_reason=choices[0].get("finish_reason"),
    )


@dataclass
class _RunStats:
    batches_done: int = 0
    batches_resumed: int = 0
    api_calls: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0

    def add_call(self, info: dict) -> None:
        self.api_calls += 1
        self.input_tokens += info.get("input_tokens") or 0
        self.output_tokens += info.get("output_tokens") or 0
        self.thinking_tokens += info.get("thinking_tokens") or 0


def _system_prompt() -> str:
    return (
        "You translate K-pop video subtitles into natural, fan-appropriate Traditional Chinese "
        "(Taiwan usage / zh-TW). Preserve tone, idioms, honorifics, and fandom slang meaning "
        "rather than translating literally. Keep translations concise enough to read as "
        "subtitles (roughly one line). Use the glossary below for names/terms whenever they "
        "appear, for consistency:\n\n" + glossary.format_for_prompt()
    )


def _user_prompt(texts: List[str], source_lang: str) -> str:
    lang_name = _LANG_NAMES.get(source_lang, source_lang)
    numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
    return (
        f"Translate each numbered {lang_name} subtitle line below into Traditional Chinese.\n"
        "This includes short interjections and bracketed sound/action descriptions "
        "(e.g. \"[sigh]\" -> \"[嗉氣]\") - translate every line, never skip one.\n"
        'Respond with ONLY a JSON array of objects, one per input line: '
        '[{"i": <line number>, "t": "<translation>"}, ...]. '
        "Use the exact same line numbers as the input, include every line number exactly "
        "once, and output no other text.\n\n" + numbered
    )


def _batch_prompt_hash(texts: List[str], source_lang: str) -> str:
    """Identifies a batch by the exact request the LLM would receive (glossary and prompt wording
    included), so a checkpoint can only ever be reused for an identical request."""
    request = _system_prompt() + "\0" + _user_prompt(texts, source_lang)
    return hashlib.sha1(request.encode("utf-8")).hexdigest()


def _parse_json_objects(raw: str, expected_len: int) -> List[str]:
    raw = _JSON_FENCE_RE.sub("", raw).strip()
    items = json.loads(raw)
    by_index = {int(item["i"]): item["t"] for item in items}
    missing = [i for i in range(expected_len) if i not in by_index]
    if missing:
        raise ValueError(f"Translation response missing line numbers: {missing}")
    return [by_index[i] for i in range(expected_len)]


def _translate_batch_anthropic(texts: List[str], source_lang: str) -> List[str]:
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to backend/.env (see .env.example)."
        )
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=_system_prompt(),
        messages=[{"role": "user", "content": _user_prompt(texts, source_lang)}],
    )
    usage = getattr(response, "usage", None)
    _record_call_info(
        model=config.ANTHROPIC_MODEL,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        stop_reason=getattr(response, "stop_reason", None),
    )
    raw = "".join(block.text for block in response.content if block.type == "text")
    return _parse_json_objects(raw, len(texts))


def _translate_batch_claude_agent(texts: List[str], source_lang: str) -> List[str]:
    """Uses the Claude Agent SDK, which drives the Claude Code CLI as a local subprocess, as a
    plain text-in/text-out translation call - same prompts/parsing as _translate_batch_anthropic,
    just a different transport. No tools are granted (subtitle text is untrusted third-party
    content, and there's nothing here for a tool to do), so this never touches the filesystem or
    network beyond the CLI's own call to Anthropic. Auth is whatever the CLI itself is configured
    with (an API key via its own env, or a `claude login` subscription session) - never passed
    through here."""
    import asyncio

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        CLINotFoundError,
        ResultMessage,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        system_prompt=_system_prompt(),
        # tools=[] means the agent never has anything to request permission for, so
        # permission_mode is left at its default rather than forced to "bypassPermissions"
        # (which the CLI refuses outright when running as root, as this backend may well do).
        tools=[],
        setting_sources=[],
        model=config.CLAUDE_AGENT_MODEL or None,
    )

    async def _run():
        reply_text = ""
        info: dict = {}
        async for message in query(prompt=_user_prompt(texts, source_lang), options=options):
            if isinstance(message, AssistantMessage):
                if message.error:
                    raise RuntimeError(f"Claude Code agent error: {message.error}")
                reply_text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
                usage = message.usage or {}
                info = {
                    "model": message.model,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "stop_reason": message.stop_reason,
                }
            elif isinstance(message, ResultMessage) and message.is_error:
                raise RuntimeError(f"Claude Code agent failed: {message.result or message.subtype}")
        return reply_text, info

    try:
        raw, info = asyncio.run(_run())
    except CLINotFoundError as exc:
        raise RuntimeError(
            "Claude Code CLI not found. Install it (npm install -g @anthropic-ai/claude-code) "
            "and run `claude login` once on this machine (or set ANTHROPIC_API_KEY for the CLI "
            "itself), or switch TRANSLATION_PROVIDER to something else."
        ) from exc

    _record_call_info(**info)
    return _parse_json_objects(raw, len(texts))


def _quota_violations(exc: Exception) -> List[dict]:
    """Flattens the QuotaFailure violations out of a Gemini 429 error's details, if any."""
    details = getattr(exc, "details", None)
    error_details = None
    if isinstance(details, dict):
        error_details = details.get("details") or details.get("error", {}).get("details")
    violations: List[dict] = []
    if isinstance(error_details, list):
        for detail in error_details:
            for v in (detail.get("violations") if isinstance(detail, dict) else None) or []:
                if isinstance(v, dict):
                    violations.append(v)
    return violations


def _is_daily_quota_exceeded(exc: Exception) -> bool:
    # e.g. quotaId "GenerateRequestsPerDayPerProjectPerModel-FreeTier" - unlike a per-minute
    # limit, this can't be waited out within a single run, so retrying is pure waste.
    return any("PerDay" in v.get("quotaId", "") for v in _quota_violations(exc))


def _extract_retry_delay_seconds(exc: Exception) -> Optional[float]:
    """Best-effort read of how long Gemini wants us to wait before retrying a 429."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        header_value = headers.get("Retry-After")
        if header_value:
            try:
                return float(header_value)
            except ValueError:
                pass

    # Gemini quota errors typically include a RetryInfo detail like {"retryDelay": "20s"}.
    details = getattr(exc, "details", None)
    error_details = None
    if isinstance(details, dict):
        error_details = details.get("details") or details.get("error", {}).get("details")
    if isinstance(error_details, list):
        for detail in error_details:
            delay = detail.get("retryDelay") if isinstance(detail, dict) else None
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    pass
    return None


def _translate_batch_gemini(texts: List[str], source_lang: str) -> List[str]:
    from google import genai
    from google.genai import types
    from google.genai.errors import ClientError

    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to backend/.env (see .env.example). "
            "Get a free key at https://aistudio.google.com/apikey"
        )
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    for attempt in range(config.GEMINI_MAX_RETRIES + 1):
        # Proactively pace requests to stay under the RPM cap, rather than only reacting
        # to 429s after the fact - this is what actually prevents most rate-limit errors.
        _gemini_limiter.acquire()
        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=_user_prompt(texts, source_lang),
                config=types.GenerateContentConfig(
                    system_instruction=_system_prompt(),
                    response_mime_type="application/json",
                ),
            )
            usage = getattr(response, "usage_metadata", None)
            candidates = getattr(response, "candidates", None) or []
            finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
            _record_call_info(
                model=config.GEMINI_MODEL,
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=getattr(usage, "candidates_token_count", None),
                thinking_tokens=getattr(usage, "thoughts_token_count", None),
                stop_reason=getattr(finish_reason, "name", finish_reason),
            )
            return _parse_json_objects(response.text, len(texts))
        except ClientError as exc:
            if exc.code != 429:
                raise
            if _is_daily_quota_exceeded(exc):
                raise RuntimeError(
                    f"Gemini free-tier DAILY request quota exhausted for {config.GEMINI_MODEL!r} "
                    "- this resets roughly once every 24h, not something retrying/backoff can "
                    "wait out. Options: wait for it to reset, switch TRANSLATION_PROVIDER=anthropic "
                    "in backend/.env, or use a different Gemini API key/project. "
                    f"(Google's response: {exc.message})"
                ) from exc
            if attempt == config.GEMINI_MAX_RETRIES:
                raise
            delay = _extract_retry_delay_seconds(exc)
            if delay is None:
                delay = min(60.0, 5.0 * (2**attempt))  # exponential fallback: 5s, 10s, 20s, 40s...
            logger.warning(
                "Gemini rate-limited (attempt %d/%d); retrying in %.1fs",
                attempt + 1,
                config.GEMINI_MAX_RETRIES,
                delay,
            )
            time.sleep(delay)


_DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


def _deepseek_backoff(attempt: int, reason: str) -> None:
    delay = min(60.0, 5.0 * (2**attempt))  # exponential backoff: 5s, 10s, 20s, 40s...
    logger.warning(
        "DeepSeek request failed (%s, attempt %d/%d); retrying in %.1fs",
        reason,
        attempt + 1,
        config.DEEPSEEK_MAX_RETRIES,
        delay,
    )
    time.sleep(delay)


def _translate_batch_deepseek(texts: List[str], source_lang: str) -> List[str]:
    import httpx

    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set. Add it to backend/.env (see .env.example). "
            "Get a key at https://platform.deepseek.com/api_keys"
        )

    # Not using response_format={"type": "json_object"}: on this model it degenerates into
    # echoing the format spec back as content (e.g. '{"type": "json_object"}') instead of
    # translating. Plain-text mode + the prompt's own JSON instruction works reliably, same as
    # the Anthropic path; _parse_json_objects still strips a markdown fence if one shows up.
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(texts, source_lang)},
        ],
        "thinking": {"type": config.DEEPSEEK_THINKING},
    }
    headers = {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}

    for attempt in range(config.DEEPSEEK_MAX_RETRIES + 1):
        try:
            response = httpx.post(_DEEPSEEK_API_URL, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            break
        except httpx.HTTPStatusError as exc:
            # 429s (rate limit) and 5xx (transient server-side issues) are worth retrying;
            # 4xx like a bad key or malformed request are not.
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise
            if attempt == config.DEEPSEEK_MAX_RETRIES:
                raise
            _deepseek_backoff(attempt, f"status {status}")
        except httpx.TransportError as exc:
            # A dropped connection (RemoteProtocolError), timeout, or connect failure - typically
            # a long response cut off mid-stream. No status code to inspect, but retrying is safe:
            # nothing was received, and the request is a pure translation with no side effects.
            if attempt == config.DEEPSEEK_MAX_RETRIES:
                raise
            _deepseek_backoff(attempt, type(exc).__name__)

    data = response.json()
    _openai_compat_call_info(data, config.DEEPSEEK_MODEL)
    return _parse_json_objects(data["choices"][0]["message"]["content"], len(texts))


def _translate_batch_local(texts: List[str], source_lang: str, model: Optional[str]) -> List[str]:
    import httpx

    model_name = model or config.LOCAL_LLM_MODEL
    if not model_name:
        raise RuntimeError(
            "No local LLM model selected. Pick one from the model dropdown, or set "
            "LOCAL_LLM_MODEL in backend/.env."
        )
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(texts, source_lang)},
        ],
    }
    response = httpx.post(
        f"{config.LOCAL_LLM_BASE_URL}/chat/completions", json=payload, timeout=180
    )
    response.raise_for_status()
    data = response.json()
    _openai_compat_call_info(data, model_name)
    return _parse_json_objects(data["choices"][0]["message"]["content"], len(texts))


def _translate_batch_once(
    texts: List[str], source_lang: str, model: Optional[str] = None, provider: Optional[str] = None
) -> List[str]:
    provider = provider or config.TRANSLATION_PROVIDER
    if provider == "gemini":
        return _translate_batch_gemini(texts, source_lang)
    if provider == "anthropic":
        return _translate_batch_anthropic(texts, source_lang)
    if provider == "deepseek":
        return _translate_batch_deepseek(texts, source_lang)
    if provider == "local":
        return _translate_batch_local(texts, source_lang, model)
    if provider == "claude_agent":
        return _translate_batch_claude_agent(texts, source_lang)
    raise RuntimeError(
        f"Unknown translation provider {provider!r}; use 'gemini', 'anthropic', 'deepseek', "
        "'local', or 'claude_agent'."
    )


_BATCH_RETRY_ATTEMPTS = 3


def _translate_batch(
    texts: List[str],
    source_lang: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    batch_label: str = "",
    stats: Optional[_RunStats] = None,
) -> List[str]:
    provider_name = provider or config.TRANSLATION_PROVIDER
    for attempt in range(1, _BATCH_RETRY_ATTEMPTS + 1):
        _call_info.value = None
        started = time.monotonic()
        try:
            result = _translate_batch_once(texts, source_lang, model, provider)
        except (ValueError, json.JSONDecodeError) as exc:
            # LLMs occasionally skip a line (often near the end of a large batch) or return
            # malformed JSON; a fresh retry usually clears it. Weaker/faster models (e.g.
            # DeepSeek's default) do this more often than Gemini/Claude, so allow a couple of
            # retries rather than just one. The failed attempt's tokens were still billed, so
            # they're counted and logged here.
            info = _take_call_info()
            will_retry = attempt < _BATCH_RETRY_ATTEMPTS
            if stats is not None:
                stats.add_call(info)
                stats.retries += 1 if will_retry else 0
            logger.warning(
                "translate batch %s FAILED provider=%s attempt=%d/%d lines=%d secs=%.1f %s "
                "reason=%s: %s; %s",
                batch_label or "-",
                provider_name,
                attempt,
                _BATCH_RETRY_ATTEMPTS,
                len(texts),
                time.monotonic() - started,
                _format_call_info(info),
                type(exc).__name__,
                exc,
                "retrying" if will_retry else "giving up",
            )
            if _is_truncated(info):
                logger.warning(
                    "translate batch %s output was TRUNCATED (stop=%s) - the batch likely exceeds "
                    "the model's max output tokens; lower TRANSLATE_BATCH_SIZE",
                    batch_label or "-",
                    info.get("stop_reason"),
                )
            if not will_retry:
                raise
            continue
        info = _take_call_info()
        if stats is not None:
            stats.add_call(info)
        logger.info(
            "translate batch %s ok provider=%s attempt=%d/%d lines=%d secs=%.1f %s",
            batch_label or "-",
            provider_name,
            attempt,
            _BATCH_RETRY_ATTEMPTS,
            len(texts),
            time.monotonic() - started,
            _format_call_info(info),
        )
        return result


def translate_cues(
    cues: List[Cue],
    source_lang: str,
    on_batch: Optional[BatchProgressCallback] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    checkpoint: Optional[BatchCheckpoint] = None,
) -> List[str]:
    results: List[str] = []
    batch_size = config.TRANSLATE_BATCH_SIZE
    batches = [cues[i : i + batch_size] for i in range(0, len(cues), batch_size)]
    stats = _RunStats()
    try:
        for batch_num, batch in enumerate(batches, start=1):
            texts = [c.text for c in batch]
            batch_label = f"{batch_num}/{len(batches)}"

            prompt_hash = _batch_prompt_hash(texts, source_lang) if checkpoint else None
            saved = checkpoint.load(prompt_hash) if checkpoint else None
            if saved is not None and len(saved) == len(texts):
                logger.info("translate batch %s resumed from checkpoint lines=%d", batch_label, len(texts))
                results.extend(saved)
                stats.batches_resumed += 1
                stats.batches_done += 1
                continue

            if on_batch:
                on_batch(batch_num, len(batches))
            translated = _translate_batch(
                texts, source_lang, model, provider, batch_label=batch_label, stats=stats
            )
            if checkpoint:
                try:
                    checkpoint.save(prompt_hash, translated)
                except Exception:
                    # Losing a checkpoint only costs a possible re-translation later; it must
                    # never fail a batch that was just translated successfully.
                    logger.warning("Could not save checkpoint for batch %s", batch_label, exc_info=True)
            results.extend(translated)
            stats.batches_done += 1
    finally:
        # Logged even when a later batch raises - the tokens already spent on earlier batches
        # are exactly what a failed run wastes.
        logger.info(
            "translate summary provider=%s batches=%d/%d resumed=%d api_calls=%d retries=%d "
            "in_tok=%d out_tok=%d think_tok=%d",
            provider or config.TRANSLATION_PROVIDER,
            stats.batches_done,
            len(batches),
            stats.batches_resumed,
            stats.api_calls,
            stats.retries,
            stats.input_tokens,
            stats.output_tokens,
            stats.thinking_tokens,
        )
    return results
