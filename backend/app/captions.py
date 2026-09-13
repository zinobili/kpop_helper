from typing import List, Optional, Tuple

import httpx
import yt_dlp

from . import config
from .vtt_utils import Cue, parse_vtt


def extract_video_info(url: str) -> dict:
    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def _pick_track_url(info: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Returns (track_url, lang, source_type).

    Prefers the original Korean text (manual, then auto-generated) over any language's
    captions, since translating from the original language directly - even noisy
    auto-generated text - preserves more nuance than re-translating an already-translated
    English caption. Only falls back to English if no Korean track exists at all.
    """
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    for lang in config.CAPTION_LANG_PREFERENCE:
        for source_type, tracks in (("manual", manual), ("auto", auto)):
            formats = tracks.get(lang)
            if formats:
                url = _pick_vtt_format(formats)
                if url:
                    return url, lang, source_type

    return None, None, None


def _pick_vtt_format(formats: list) -> Optional[str]:
    for fmt in formats:
        if fmt.get("ext") == "vtt":
            return fmt.get("url")
    return formats[0].get("url") if formats else None


def fetch_captions(url: str) -> Tuple[Optional[List[Cue]], Optional[str], Optional[str], dict]:
    """Fetch the best available caption track for a video.

    Returns (cues, lang, source_type, info). cues is None if no captions exist at all.
    """
    info = extract_video_info(url)
    track_url, lang, source_type = _pick_track_url(info)
    if not track_url:
        return None, None, None, info

    resp = httpx.get(track_url, timeout=30)
    resp.raise_for_status()
    cues = parse_vtt(resp.text)
    return cues, lang, source_type, info
