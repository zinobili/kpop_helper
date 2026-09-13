import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional

from . import config
from .vtt_utils import Cue

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subtitles (
    video_id TEXT PRIMARY KEY,
    title TEXT,
    source_lang TEXT,
    source_type TEXT,
    cues_ko_json TEXT,
    cues_zh_json TEXT,
    created_at TEXT
);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _cues_to_json(cues: List[Cue]) -> str:
    return json.dumps([{"start": c.start, "end": c.end, "text": c.text} for c in cues])


def _json_to_cues(raw: str) -> List[Cue]:
    return [Cue(**c) for c in json.loads(raw)]


def get(video_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT title, source_lang, source_type, cues_ko_json, cues_zh_json, created_at "
            "FROM subtitles WHERE video_id = ?",
            (video_id,),
        ).fetchone()
    if not row:
        return None
    title, source_lang, source_type, cues_ko_json, cues_zh_json, created_at = row
    return {
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "cues_ko": _json_to_cues(cues_ko_json),
        "cues_zh": _json_to_cues(cues_zh_json),
        "created_at": created_at,
    }


def put(
    video_id: str,
    title: str,
    source_lang: str,
    source_type: str,
    cues_ko: List[Cue],
    cues_zh: List[Cue],
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO subtitles (video_id, title, source_lang, source_type, cues_ko_json, "
            "cues_zh_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(video_id) DO UPDATE SET title=excluded.title, "
            "source_lang=excluded.source_lang, source_type=excluded.source_type, "
            "cues_ko_json=excluded.cues_ko_json, cues_zh_json=excluded.cues_zh_json, "
            "created_at=excluded.created_at",
            (
                video_id,
                title,
                source_lang,
                source_type,
                _cues_to_json(cues_ko),
                _cues_to_json(cues_zh),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
