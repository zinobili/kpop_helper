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


def _candidate_identities(caption_lang: Optional[str], force_stt: bool):
    """Every (source_lang, source_type) a request could resolve to, in preference order -
    used to probe the cache without ever touching yt-dlp/YouTube."""
    if force_stt:
        return [("ko", "whisper")]
    langs = [caption_lang] if caption_lang else config.CAPTION_LANG_PREFERENCE
    candidates = [(lang, source_type) for lang in langs for source_type in ("manual", "auto")]
    if not caption_lang or caption_lang == "ko":
        candidates.append(("ko", "whisper"))
    return candidates


def resolve_transcript_identity(
    url: str, caption_lang: Optional[str] = None, force_stt: bool = False
) -> dict:
    """Figures out which (video_id, source_lang, source_type) a request would resolve to,
    without downloading a caption track's body or running Whisper - just one yt-dlp metadata
    call. Used by the real pipeline's cache short-circuit and by /translate-preview.

    source_lang/source_type come back None only when caption_lang was explicitly requested and
    isn't available for this video (mirrors process_video's own error condition).
    """
    info = captions.extract_video_info(url)
    video_id = info["id"]
    title = info.get("title", "")

    if force_stt:
        return {"video_id": video_id, "title": title, "source_lang": "ko", "source_type": "whisper"}

    lang_preference = [caption_lang] if caption_lang else None
    _track_url, lang, source_type = captions.pick_caption_track(info, lang_preference)
    if lang and source_type:
        return {"video_id": video_id, "title": title, "source_lang": lang, "source_type": source_type}

    if caption_lang and caption_lang != "ko":
        return {"video_id": video_id, "title": title, "source_lang": None, "source_type": None}

    # No captions at all (or none in the requested/preferred language) - Whisper is the
    # fallback for everything except an explicit non-Korean request, handled above.
    return {"video_id": video_id, "title": title, "source_lang": "ko", "source_type": "whisper"}


def _resolve_transcript(
    url: str,
    caption_lang: Optional[str],
    force_stt: bool,
    force_refresh: bool,
    report: Callable[[str, Optional[str]], None],
) -> dict:
    """Finds-or-creates the transcript for this request, reusing the cache whenever possible.

    Mirrors process_video's old caption/whisper resolution order exactly, just checking the
    cache (keyed by video_id + source_lang + source_type) before doing the expensive fetch/
    transcribe step, and writing the result back under that same key afterward.
    """
    if not force_refresh:
        # A network-free video-id parse first, so a cache hit never has to touch
        # yt-dlp/YouTube at all - cached videos keep working even if YouTube is currently
        # bot-walling yt-dlp.
        quick_id = captions.extract_video_id_from_url(url)
        if quick_id:
            for lang, source_type in _candidate_identities(caption_lang, force_stt):
                cached = cache.get_transcript(quick_id, lang, source_type)
                if cached:
                    return cached

    if force_stt:
        report("looking_up_video")
        info = captions.extract_video_info(url)
        video_id, title = info["id"], info.get("title", "")
        if not force_refresh:
            cached = cache.get_transcript(video_id, "ko", "whisper")
            if cached:
                return cached
        report("transcribing_audio")
        audio_path = transcribe.download_audio(url, video_id)
        cues = transcribe.transcribe(audio_path)
        if not cues:
            raise RuntimeError("Could not obtain any captions or transcription for this video.")
        transcript_id = cache.put_transcript(video_id, title, "ko", "whisper", cues)
        return {
            "transcript_id": transcript_id,
            "video_id": video_id,
            "title": title,
            "source_lang": "ko",
            "source_type": "whisper",
            "cues": cues,
        }

    report("looking_up_video")
    info = captions.extract_video_info(url)
    video_id, title = info["id"], info.get("title", "")

    lang_preference = [caption_lang] if caption_lang else None
    report("fetching_captions")
    cues, lang, source_type, _info = captions.fetch_captions(url, lang_preference=lang_preference)

    if cues:
        if not force_refresh:
            cached = cache.get_transcript(video_id, lang, source_type)
            if cached:
                return cached
        transcript_id = cache.put_transcript(video_id, title, lang, source_type, cues)
        return {
            "transcript_id": transcript_id,
            "video_id": video_id,
            "title": title,
            "source_lang": lang,
            "source_type": source_type,
            "cues": cues,
        }

    # Whisper only ever transcribes the original Korean audio (see transcribe.transcribe), so
    # it's a sensible fallback when no caption preference was given, or "ko" was requested but
    # isn't available - but never when the user explicitly asked for a different language's
    # captions, since silently swapping in a Korean transcript would contradict that choice.
    if caption_lang and caption_lang != "ko":
        raise RuntimeError(
            f"No {caption_lang!r} captions found for this video. Local transcription can only "
            "produce a Korean transcript, so it can't stand in for this choice - try a "
            "different caption source, or leave it on auto."
        )

    if not force_refresh:
        cached = cache.get_transcript(video_id, "ko", "whisper")
        if cached:
            return cached
    report("transcribing_audio")
    audio_path = transcribe.download_audio(url, video_id)
    cues = transcribe.transcribe(audio_path)
    if not cues:
        raise RuntimeError("Could not obtain any captions or transcription for this video.")
    transcript_id = cache.put_transcript(video_id, title, "ko", "whisper", cues)
    return {
        "transcript_id": transcript_id,
        "video_id": video_id,
        "title": title,
        "source_lang": "ko",
        "source_type": "whisper",
        "cues": cues,
    }


def preview_translation_options(
    url: str,
    caption_lang: Optional[str] = None,
    force_stt: bool = False,
    provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> dict:
    """What a /process call with these settings would do, without doing any of the expensive
    work: which transcript it would use, whether a translation already matches these exact
    settings, and what other cached translations exist for that same transcript.
    """
    identity = resolve_transcript_identity(url, caption_lang=caption_lang, force_stt=force_stt)
    video_id, title = identity["video_id"], identity["title"]
    source_lang, source_type = identity["source_lang"], identity["source_type"]

    effective_provider = provider or config.TRANSLATION_PROVIDER
    resolved_model = _resolved_model(effective_provider, llm_model)

    if not source_lang or not source_type:
        return {
            "video_id": video_id,
            "title": title,
            "source_lang": None,
            "source_type": None,
            "exact_match": False,
            "alternates": [],
        }

    transcript = cache.get_transcript(video_id, source_lang, source_type)
    if not transcript:
        return {
            "video_id": video_id,
            "title": title,
            "source_lang": source_lang,
            "source_type": source_type,
            "exact_match": False,
            "alternates": [],
        }

    variants = cache.list_translations_for_transcript(transcript["transcript_id"])

    def _is_requested(v: dict) -> bool:
        return v["translation_provider"] == effective_provider and v["translation_model"] == resolved_model

    return {
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "exact_match": any(_is_requested(v) for v in variants),
        "alternates": [v for v in variants if not _is_requested(v)],
    }


def get_variant_result(variant_id: str) -> Optional[dict]:
    """A cached translation's full cues, in the same shape a finished job's `result` has -
    lets a client load an already-cached variant directly, no job/polling needed."""
    variant = cache.get_variant(variant_id)
    if not variant:
        return None
    transcript = cache.get_transcript(variant["video_id"], variant["source_lang"], variant["source_type"])
    cues_ko = transcript["cues"] if transcript else []
    return {
        "video_id": variant["video_id"],
        "title": variant["title"],
        "source_lang": variant["source_lang"],
        "source_type": variant["source_type"],
        "cached": True,
        "translated": True,
        "translation_provider": variant["translation_provider"],
        "translation_model": variant["translation_model"],
        "transcript_id": variant["transcript_id"],
        "variant_id": variant["variant_id"],
        "cues": _cues_to_dicts(cues_ko, [c.text for c in variant["cues"]]),
    }


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

    transcript = _resolve_transcript(url, caption_lang, force_stt, force_refresh, report)
    video_id = transcript["video_id"]
    title = transcript["title"]
    source_lang = transcript["source_lang"]
    source_type = transcript["source_type"]
    cues_ko: List[Cue] = transcript["cues"]

    if skip_translation:
        report("skipping_translation")
        report("done")
        return {
            "video_id": video_id,
            "title": title,
            "source_lang": source_lang,
            "source_type": source_type,
            "cached": False,
            "translated": False,
            "translation_provider": None,
            "translation_model": None,
            "transcript_id": transcript["transcript_id"],
            "variant_id": None,
            "cues": _cues_to_dicts(cues_ko, [c.text for c in cues_ko]),
        }

    # A per-request provider override falls back to the .env default when not given.
    effective_provider = provider or config.TRANSLATION_PROVIDER
    resolved_model = _resolved_model(effective_provider, llm_model)

    cached_translation = None
    if not force_refresh:
        cached_translation = cache.get_translation(
            transcript["transcript_id"], effective_provider, resolved_model
        )

    if cached_translation:
        report("done")
        return {
            "video_id": video_id,
            "title": title,
            "source_lang": source_lang,
            "source_type": source_type,
            "cached": True,
            "translated": True,
            "translation_provider": effective_provider,
            "translation_model": resolved_model,
            "transcript_id": transcript["transcript_id"],
            "variant_id": cached_translation["variant_id"],
            "cues": _cues_to_dicts(cues_ko, [c.text for c in cached_translation["cues"]]),
        }

    def on_batch(batch_num: int, total_batches: int) -> None:
        detail = f"batch {batch_num}/{total_batches}"
        if effective_provider == "gemini" and total_batches > config.GEMINI_RPM:
            detail += f" (throttled to {config.GEMINI_RPM}/min on Gemini's free tier)"
        report("translating", detail)

    translated_texts = translate.translate_cues(
        cues_ko, source_lang, on_batch=on_batch, model=llm_model, provider=provider
    )
    variant_id = cache.put_translation(
        transcript["transcript_id"],
        effective_provider,
        resolved_model,
        [Cue(start=c.start, end=c.end, text=t) for c, t in zip(cues_ko, translated_texts)],
    )

    report("done")
    return {
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "cached": False,
        "translated": True,
        "translation_provider": effective_provider,
        "translation_model": resolved_model,
        "transcript_id": transcript["transcript_id"],
        "variant_id": variant_id,
        "cues": _cues_to_dicts(cues_ko, translated_texts),
    }
