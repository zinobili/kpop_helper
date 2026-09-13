from typing import Callable, List, Optional

from . import cache, captions, transcribe, translate
from .vtt_utils import Cue

ProgressCallback = Callable[[str], None]


def _cues_to_dicts(cues_ko: List[Cue], cues_zh: List[str]) -> List[dict]:
    return [
        {"start": ko.start, "end": ko.end, "text_ko": ko.text, "text_zh": zh}
        for ko, zh in zip(cues_ko, cues_zh)
    ]


def process_video(
    url: str, force_refresh: bool = False, on_progress: Optional[ProgressCallback] = None
) -> dict:
    def report(stage: str) -> None:
        if on_progress:
            on_progress(stage)

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

    report("translating")
    translated_texts = translate.translate_cues(cues_ko, source_lang)
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
