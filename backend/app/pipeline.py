from typing import Callable, List, Optional

from . import cache, captions, config, transcribe, translate
from .vtt_utils import Cue

ProgressCallback = Callable[[str, Optional[str]], None]


def _resolved_model(provider: str, llm_model: Optional[str]) -> Optional[str]:
    """The concrete model id that will actually handle translation, for dashboard display."""
    if provider == "gemini":
        return config.GEMINI_MODEL
    if provider == "anthropic":
        return config.ANTHROPIC_MODEL
    if provider == "deepseek":
        return config.DEEPSEEK_MODEL
    if provider == "local":
        return llm_model or config.LOCAL_LLM_MODEL or None
    return None


def _cues_to_dicts(cues_ko: List[Cue], cues_zh: List[str]) -> List[dict]:
    return [
        {"start": ko.start, "end": ko.end, "text_ko": ko.text, "text_zh": zh}
        for ko, zh in zip(cues_ko, cues_zh)
    ]


def process_video(
    url: str,
    force_refresh: bool = False,
    on_progress: Optional[ProgressCallback] = None,
    force_stt: bool = False,
    skip_translation: bool = False,
    caption_lang: Optional[str] = None,
    llm_model: Optional[str] = None,
    provider: Optional[str] = None,
) -> dict:
    def report(stage: str, detail: Optional[str] = None) -> None:
        if on_progress:
            on_progress(stage, detail)

    # A per-request provider override falls back to the .env default when not given.
    effective_provider = provider or config.TRANSLATION_PROVIDER

    # force_stt/skip_translation/caption_lang/provider are explicit, deliberate overrides
    # (debug an STT-only source, avoid the LLM entirely, pick a specific caption track, or try
    # a different provider) - they always run fresh and never read or write the shared cache,
    # so they can't shadow a normal request's real translated result under the same video_id,
    # and a normal request later can't accidentally surface a result built from one of these.
    use_cache = (
        not force_refresh
        and not force_stt
        and not skip_translation
        and not caption_lang
        and not provider
    )

    # Try a network-free video ID parse first, so a cache hit never has to touch
    # yt-dlp/YouTube at all - this also means cached videos keep working even if YouTube is
    # currently throwing up a bot-detection wall against yt-dlp.
    if use_cache:
        quick_id = captions.extract_video_id_from_url(url)
        if quick_id:
            cached = cache.get(quick_id)
            if cached:
                report("done")
                return {
                    "video_id": quick_id,
                    "title": cached["title"],
                    "source_lang": cached["source_lang"],
                    "source_type": cached["source_type"],
                    "cached": True,
                    "translated": True,
                    "translation_provider": cached["translation_provider"],
                    "translation_model": cached["translation_model"],
                    "cues": _cues_to_dicts(cached["cues_ko"], [c.text for c in cached["cues_zh"]]),
                }

    report("looking_up_video")
    info = captions.extract_video_info(url)
    video_id = info["id"]
    title = info.get("title", "")

    if use_cache:
        cached = cache.get(video_id)
        if cached:
            report("done")
            return {
                "video_id": video_id,
                "title": cached["title"],
                "source_lang": cached["source_lang"],
                "source_type": cached["source_type"],
                "cached": True,
                "translated": True,
                "translation_provider": cached["translation_provider"],
                "translation_model": cached["translation_model"],
                "cues": _cues_to_dicts(cached["cues_ko"], [c.text for c in cached["cues_zh"]]),
            }

    cues_ko: List[Cue] = []
    source_lang = source_type = None

    if not force_stt:
        report("fetching_captions")
        lang_preference = [caption_lang] if caption_lang else None
        cues_ko, source_lang, source_type, _info = captions.fetch_captions(
            url, lang_preference=lang_preference
        )

    # Whisper only ever transcribes the original Korean audio (see transcribe.transcribe), so
    # it's a sensible fallback when no caption preference was given, or "ko" was requested but
    # isn't available - but never when the user explicitly asked for a different language's
    # captions, since silently swapping in a Korean transcript would contradict that choice.
    if not cues_ko and caption_lang and caption_lang != "ko":
        raise RuntimeError(
            f"No {caption_lang!r} captions found for this video. Local transcription can only "
            "produce a Korean transcript, so it can't stand in for this choice - try a "
            "different caption source, or leave it on auto."
        )

    if not cues_ko:
        report("transcribing_audio")
        audio_path = transcribe.download_audio(url, video_id)
        cues_ko = transcribe.transcribe(audio_path)
        source_lang = "ko"
        source_type = "whisper"

    if not cues_ko:
        raise RuntimeError("Could not obtain any captions or transcription for this video.")

    translation_provider = translation_model = None
    if skip_translation:
        report("skipping_translation")
        translated_texts = [c.text for c in cues_ko]
    else:
        def on_batch(batch_num: int, total_batches: int) -> None:
            detail = f"batch {batch_num}/{total_batches}"
            if effective_provider == "gemini" and total_batches > config.GEMINI_RPM:
                detail += f" (throttled to {config.GEMINI_RPM}/min on Gemini's free tier)"
            report("translating", detail)

        translated_texts = translate.translate_cues(
            cues_ko, source_lang, on_batch=on_batch, model=llm_model, provider=provider
        )
        translation_provider = effective_provider
        translation_model = _resolved_model(effective_provider, llm_model)

    cues_zh = [Cue(start=c.start, end=c.end, text=t) for c, t in zip(cues_ko, translated_texts)]

    if not skip_translation and not caption_lang and not provider:
        cache.put(
            video_id,
            title,
            source_lang,
            source_type,
            cues_ko,
            cues_zh,
            translation_provider=translation_provider,
            translation_model=translation_model,
        )

    report("done")
    return {
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "cached": False,
        "translated": not skip_translation,
        "translation_provider": translation_provider,
        "translation_model": translation_model,
        "cues": _cues_to_dicts(cues_ko, translated_texts),
    }
