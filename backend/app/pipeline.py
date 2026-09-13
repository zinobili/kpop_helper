from typing import Callable, List, Optional

from . import cache, captions, config, transcribe, translate
from .vtt_utils import Cue

ProgressCallback = Callable[[str, Optional[str]], None]


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
) -> dict:
    def report(stage: str, detail: Optional[str] = None) -> None:
        if on_progress:
            on_progress(stage, detail)

    # force_stt/skip_translation are explicit, deliberate overrides (debug an STT-only source,
    # or avoid the LLM entirely) - they always run fresh and never read or write the shared
    # cache, so they can't shadow a normal request's real translated result under the same
    # video_id, and a normal request later can't accidentally surface an untranslated one.
    use_cache = not force_refresh and not force_stt and not skip_translation

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
                "cues": _cues_to_dicts(cached["cues_ko"], [c.text for c in cached["cues_zh"]]),
            }

    cues_ko: List[Cue] = []
    source_lang = source_type = None

    if not force_stt:
        report("fetching_captions")
        cues_ko, source_lang, source_type, _info = captions.fetch_captions(url)

    if not cues_ko:
        report("transcribing_audio")
        audio_path = transcribe.download_audio(url, video_id)
        cues_ko = transcribe.transcribe(audio_path)
        source_lang = "ko"
        source_type = "whisper"

    if not cues_ko:
        raise RuntimeError("Could not obtain any captions or transcription for this video.")

    if skip_translation:
        report("skipping_translation")
        translated_texts = [c.text for c in cues_ko]
    else:
        def on_batch(batch_num: int, total_batches: int) -> None:
            detail = f"batch {batch_num}/{total_batches}"
            if config.TRANSLATION_PROVIDER == "gemini" and total_batches > config.GEMINI_RPM:
                detail += f" (throttled to {config.GEMINI_RPM}/min on Gemini's free tier)"
            report("translating", detail)

        translated_texts = translate.translate_cues(cues_ko, source_lang, on_batch=on_batch)

    cues_zh = [Cue(start=c.start, end=c.end, text=t) for c, t in zip(cues_ko, translated_texts)]

    if not skip_translation:
        cache.put(video_id, title, source_lang, source_type, cues_ko, cues_zh)

    report("done")
    return {
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "cached": False,
        "translated": not skip_translation,
        "cues": _cues_to_dicts(cues_ko, translated_texts),
    }
