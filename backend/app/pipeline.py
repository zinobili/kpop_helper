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
    url: str, force_refresh: bool = False, on_progress: Optional[ProgressCallback] = None
) -> dict:
    def report(stage: str, detail: Optional[str] = None) -> None:
        if on_progress:
            on_progress(stage, detail)

    # Try a network-free video ID parse first, so a cache hit never has to touch
    # yt-dlp/YouTube at all - this also means cached videos keep working even if YouTube is
    # currently throwing up a bot-detection wall against yt-dlp.
    if not force_refresh:
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
                    "cues": _cues_to_dicts(cached["cues_ko"], [c.text for c in cached["cues_zh"]]),
                }

    report("looking_up_video")
    info = captions.extract_video_info(url)
    video_id = info["id"]
    title = info.get("title", "")

    if not force_refresh:
        cached = cache.get(video_id)
        if cached:
            report("done")
            return {
                "video_id": video_id,
                "title": cached["title"],
                "source_lang": cached["source_lang"],
                "source_type": cached["source_type"],
                "cached": True,
                "cues": _cues_to_dicts(cached["cues_ko"], [c.text for c in cached["cues_zh"]]),
            }

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

    def on_batch(batch_num: int, total_batches: int) -> None:
        detail = f"batch {batch_num}/{total_batches}"
        if config.TRANSLATION_PROVIDER == "gemini" and total_batches > config.GEMINI_RPM:
            detail += f" (throttled to {config.GEMINI_RPM}/min on Gemini's free tier)"
        report("translating", detail)

    translated_texts = translate.translate_cues(cues_ko, source_lang, on_batch=on_batch)
    cues_zh = [Cue(start=c.start, end=c.end, text=t) for c, t in zip(cues_ko, translated_texts)]

    cache.put(video_id, title, source_lang, source_type, cues_ko, cues_zh)

    report("done")
    return {
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "cached": False,
        "cues": _cues_to_dicts(cues_ko, translated_texts),
    }
