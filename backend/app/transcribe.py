from pathlib import Path
from typing import List

import yt_dlp

from . import config
from .vtt_utils import Cue

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(config.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def download_audio(url: str, video_id: str) -> Path:
    out_template = str(config.AUDIO_DIR / f"{video_id}.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }
        ],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)
    return config.AUDIO_DIR / f"{video_id}.wav"


def transcribe(audio_path: Path) -> List[Cue]:
    model = _get_model()
    segments, _info = model.transcribe(str(audio_path), language="ko", task="transcribe")
    cues = [Cue(start=seg.start, end=seg.end, text=seg.text.strip()) for seg in segments if seg.text.strip()]
    return cues
