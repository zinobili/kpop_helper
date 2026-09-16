import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
import yt_dlp

from . import config
from .vtt_utils import Cue, parse_vtt

logger = logging.getLogger("kpop_helper.captions")

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id_from_url(url: str) -> Optional[str]:
    """Best-effort, network-free video ID parse for the common YouTube URL shapes.

    Used only to short-circuit to the cache before ever calling yt-dlp; any URL shape this
    doesn't recognize falls back to yt-dlp's own (network) extraction, so this never needs
    to be exhaustive.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in ("youtu.be",):
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif "youtube.com" in host:
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            candidate = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
        else:
            return None
    else:
        return None
    return candidate if _VIDEO_ID_RE.match(candidate) else None


def _ydl_opts(**extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, **extra}
    if config.YTDLP_COOKIES_FILE:
        if Path(config.YTDLP_COOKIES_FILE).is_file():
            opts["cookiefile"] = config.YTDLP_COOKIES_FILE
        else:
            logger.warning(
                "YTDLP_COOKIES_FILE=%s is set but doesn't exist yet - proceeding without "
                "cookies (see README Troubleshooting to export one).",
                config.YTDLP_COOKIES_FILE,
            )
    elif config.YTDLP_COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (config.YTDLP_COOKIES_FROM_BROWSER,)
    return opts


def extract_video_info(url: str) -> dict:
    ydl_opts = _ydl_opts(skip_download=True)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def pick_caption_track(
    info: dict, lang_preference: Optional[List[str]] = None
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Returns (track_url, lang, source_type).

    By default, prefers the original Korean text (manual, then auto-generated) over any
    language's captions, since translating from the original language directly - even noisy
    auto-generated text - preserves more nuance than re-translating an already-translated
    English caption. Only falls back to English if no Korean track exists at all.

    Pass lang_preference to override that order - e.g. ["en"] to require the English track
    specifically, with no fallback to Korean.
    """
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    for lang in lang_preference or config.CAPTION_LANG_PREFERENCE:
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


def fetch_captions(
    url: str, lang_preference: Optional[List[str]] = None
) -> Tuple[Optional[List[Cue]], Optional[str], Optional[str], dict]:
    """Fetch the best available caption track for a video.

    Returns (cues, lang, source_type, info). cues is None if no captions exist at all
    (or none exist in lang_preference's language, if that's passed).
    """
    info = extract_video_info(url)
    track_url, lang, source_type = pick_caption_track(info, lang_preference)
    if not track_url:
        return None, None, None, info

    resp = httpx.get(track_url, timeout=30)
    resp.raise_for_status()
    cues = parse_vtt(resp.text)
    return cues, lang, source_type, info
