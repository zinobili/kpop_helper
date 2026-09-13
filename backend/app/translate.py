import json
import logging
import re
import time
from typing import Callable, List, Optional

from . import config, glossary
from .rate_limiter import RateLimiter
from .vtt_utils import Cue

logger = logging.getLogger("kpop_helper.translate")

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_LANG_NAMES = {"ko": "Korean", "en": "English"}

# Shared across all jobs/threads: the RPM cap is per Gemini API key/project, not per video.
_gemini_limiter = RateLimiter(config.GEMINI_RPM, period_seconds=60.0)

BatchProgressCallback = Callable[[int, int], None]


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
    raw = "".join(block.text for block in response.content if block.type == "text")
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


def _translate_batch_once(texts: List[str], source_lang: str) -> List[str]:
    if config.TRANSLATION_PROVIDER == "gemini":
        return _translate_batch_gemini(texts, source_lang)
    if config.TRANSLATION_PROVIDER == "anthropic":
        return _translate_batch_anthropic(texts, source_lang)
    raise RuntimeError(
        f"Unknown TRANSLATION_PROVIDER={config.TRANSLATION_PROVIDER!r}; use 'gemini' or 'anthropic'."
    )


def _translate_batch(texts: List[str], source_lang: str) -> List[str]:
    try:
        return _translate_batch_once(texts, source_lang)
    except (ValueError, json.JSONDecodeError):
        # LLMs occasionally skip a line or return malformed JSON; one retry clears most of these.
        return _translate_batch_once(texts, source_lang)


def translate_cues(
    cues: List[Cue], source_lang: str, on_batch: Optional[BatchProgressCallback] = None
) -> List[str]:
    results: List[str] = []
    batch_size = config.TRANSLATE_BATCH_SIZE
    batches = [cues[i : i + batch_size] for i in range(0, len(cues), batch_size)]
    for batch_num, batch in enumerate(batches, start=1):
        if on_batch:
            on_batch(batch_num, len(batches))
        texts = [c.text for c in batch]
        results.extend(_translate_batch(texts, source_lang))
    return results
