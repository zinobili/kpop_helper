import json
import re
from typing import List

from . import config, glossary
from .vtt_utils import Cue

_BATCH_SIZE = 40

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_LANG_NAMES = {"ko": "Korean", "en": "English"}


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


def _translate_batch_gemini(texts: List[str], source_lang: str) -> List[str]:
    from google import genai
    from google.genai import types

    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to backend/.env (see .env.example). "
            "Get a free key at https://aistudio.google.com/apikey"
        )
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=_user_prompt(texts, source_lang),
        config=types.GenerateContentConfig(
            system_instruction=_system_prompt(),
            response_mime_type="application/json",
        ),
    )
    return _parse_json_objects(response.text, len(texts))


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


def translate_cues(cues: List[Cue], source_lang: str) -> List[str]:
    results: List[str] = []
    for i in range(0, len(cues), _BATCH_SIZE):
        batch = cues[i : i + _BATCH_SIZE]
        texts = [c.text for c in batch]
        results.extend(_translate_batch(texts, source_lang))
    return results
