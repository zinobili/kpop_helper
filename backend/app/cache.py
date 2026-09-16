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
    translation_provider TEXT,
    translation_model TEXT,
    created_at TEXT
);
"""


_migrated_db_paths: set = set()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Adds columns introduced after a DB's first creation, for installs with an older file."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(subtitles)").fetchall()}
    for column in ("translation_provider", "translation_model"):
        if column not in existing:
            conn.execute(f"ALTER TABLE subtitles ADD COLUMN {column} TEXT")


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.execute(_SCHEMA)
        # Once a given DB file's columns are confirmed present, every later connection to it
        # (within this process) can skip the PRAGMA/ALTER check - the schema can't regress.
        if config.DB_PATH not in _migrated_db_paths:
            _ensure_columns(conn)
            _migrated_db_paths.add(config.DB_PATH)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _cues_to_json(cues: List[Cue]) -> str:
    return json.dumps([{"start": c.start, "end": c.end, "text": c.text} for c in cues])


def _json_to_cues(raw: str) -> List[Cue]:
    return [Cue(**c) for c in json.loads(raw)]


def list_all() -> List[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT video_id, title, source_lang, source_type, translation_provider, "
            "translation_model, created_at FROM subtitles ORDER BY created_at DESC"
        ).fetchall()
    return [
        {
            "video_id": video_id,
            "title": title,
            "source_lang": source_lang,
            "source_type": source_type,
            "translated": True,  # cache.put is only ever called with a translated result
            "translation_provider": translation_provider,
            "translation_model": translation_model,
            "created_at": created_at,
        }
        for (
            video_id,
            title,
            source_lang,
            source_type,
            translation_provider,
            translation_model,
            created_at,
        ) in rows
    ]


def get(video_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT title, source_lang, source_type, cues_ko_json, cues_zh_json, "
            "translation_provider, translation_model, created_at "
            "FROM subtitles WHERE video_id = ?",
            (video_id,),
        ).fetchone()
    if not row:
        return None
    (
        title,
        source_lang,
        source_type,
        cues_ko_json,
        cues_zh_json,
        translation_provider,
        translation_model,
        created_at,
    ) = row
    return {
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "cues_ko": _json_to_cues(cues_ko_json),
        "cues_zh": _json_to_cues(cues_zh_json),
        "translation_provider": translation_provider,
        "translation_model": translation_model,
        "created_at": created_at,
    }


def put(
    video_id: str,
    title: str,
    source_lang: str,
    source_type: str,
    cues_ko: List[Cue],
    cues_zh: List[Cue],
    translation_provider: Optional[str] = None,
    translation_model: Optional[str] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO subtitles (video_id, title, source_lang, source_type, cues_ko_json, "
            "cues_zh_json, translation_provider, translation_model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(video_id) DO UPDATE SET title=excluded.title, "
            "source_lang=excluded.source_lang, source_type=excluded.source_type, "
            "cues_ko_json=excluded.cues_ko_json, cues_zh_json=excluded.cues_zh_json, "
            "translation_provider=excluded.translation_provider, "
            "translation_model=excluded.translation_model, created_at=excluded.created_at",
            (
                video_id,
                title,
                source_lang,
                source_type,
                _cues_to_json(cues_ko),
                _cues_to_json(cues_zh),
                translation_provider,
                translation_model,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
