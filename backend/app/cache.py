import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional

from . import config
from .vtt_utils import Cue

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    transcript_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    title TEXT,
    source_lang TEXT NOT NULL,
    source_type TEXT NOT NULL,
    cues_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(video_id, source_lang, source_type)
);

CREATE TABLE IF NOT EXISTS translations (
    variant_id TEXT PRIMARY KEY,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    translation_provider TEXT NOT NULL,
    translation_model TEXT,
    cues_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(transcript_id, translation_provider, translation_model)
);

CREATE INDEX IF NOT EXISTS idx_transcripts_video ON transcripts(video_id);
CREATE INDEX IF NOT EXISTS idx_translations_transcript ON translations(transcript_id);
"""


_migrated_db_paths: set = set()


def _legacy_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subtitles'"
    ).fetchone()
    return row is not None


def _migrate_legacy_subtitles(conn: sqlite3.Connection) -> None:
    """Backfills the old single-row-per-video `subtitles` cache into `transcripts`/
    `translations`, for DBs created before the multi-variant cache existed. INSERT OR IGNORE
    makes this a no-op on every startup after the first."""
    # Some legacy DBs predate translation_provider/translation_model existing at all (an even
    # older single-table schema) - select only the columns this file's table actually has.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(subtitles)").fetchall()}
    has_provider_columns = "translation_provider" in columns and "translation_model" in columns
    provider_select = (
        "translation_provider, translation_model" if has_provider_columns else "NULL, NULL"
    )
    rows = conn.execute(
        "SELECT video_id, title, source_lang, source_type, cues_ko_json, cues_zh_json, "
        f"{provider_select}, created_at FROM subtitles"
    ).fetchall()
    for (
        video_id,
        title,
        source_lang,
        source_type,
        cues_ko_json,
        cues_zh_json,
        translation_provider,
        translation_model,
        created_at,
    ) in rows:
        if not source_lang or not source_type:
            continue
        transcript_id = _transcript_id(video_id, source_lang, source_type)
        conn.execute(
            "INSERT OR IGNORE INTO transcripts (transcript_id, video_id, title, source_lang, "
            "source_type, cues_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (transcript_id, video_id, title, source_lang, source_type, cues_ko_json, created_at),
        )
        # Rows predating translation_provider/translation_model tracking get a placeholder
        # provider rather than NULL, since (transcript_id, provider, model) is the row's identity.
        provider = translation_provider or "unknown"
        variant_id = _variant_id(transcript_id, provider, translation_model)
        conn.execute(
            "INSERT OR IGNORE INTO translations (variant_id, transcript_id, "
            "translation_provider, translation_model, cues_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (variant_id, transcript_id, provider, translation_model, cues_zh_json, created_at),
        )


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.executescript(_SCHEMA)
        # Once a given DB file's migration is confirmed done, every later connection to it
        # (within this process) can skip re-checking - the legacy table never comes back.
        if config.DB_PATH not in _migrated_db_paths:
            if _legacy_table_exists(conn):
                _migrate_legacy_subtitles(conn)
            _migrated_db_paths.add(config.DB_PATH)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _cues_to_json(cues: List[Cue]) -> str:
    return json.dumps([{"start": c.start, "end": c.end, "text": c.text} for c in cues])


def _json_to_cues(raw: str) -> List[Cue]:
    return [Cue(**c) for c in json.loads(raw)]


def _transcript_id(video_id: str, source_lang: str, source_type: str) -> str:
    return hashlib.sha1(f"{video_id}|{source_lang}|{source_type}".encode()).hexdigest()[:16]


def _variant_id(transcript_id: str, provider: str, model: Optional[str]) -> str:
    return hashlib.sha1(f"{transcript_id}|{provider}|{model or ''}".encode()).hexdigest()[:16]


def get_transcript(video_id: str, source_lang: str, source_type: str) -> Optional[dict]:
    transcript_id = _transcript_id(video_id, source_lang, source_type)
    with _connect() as conn:
        row = conn.execute(
            "SELECT transcript_id, video_id, title, source_lang, source_type, cues_json, "
            "created_at FROM transcripts WHERE transcript_id = ?",
            (transcript_id,),
        ).fetchone()
    if not row:
        return None
    transcript_id, video_id, title, source_lang, source_type, cues_json, created_at = row
    return {
        "transcript_id": transcript_id,
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "cues": _json_to_cues(cues_json),
        "created_at": created_at,
    }


def put_transcript(
    video_id: str, title: str, source_lang: str, source_type: str, cues: List[Cue]
) -> str:
    transcript_id = _transcript_id(video_id, source_lang, source_type)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO transcripts (transcript_id, video_id, title, source_lang, source_type, "
            "cues_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(transcript_id) DO UPDATE SET title=excluded.title, "
            "cues_json=excluded.cues_json, created_at=excluded.created_at",
            (
                transcript_id,
                video_id,
                title,
                source_lang,
                source_type,
                _cues_to_json(cues),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return transcript_id


def get_translation(transcript_id: str, provider: str, model: Optional[str]) -> Optional[dict]:
    variant_id = _variant_id(transcript_id, provider, model)
    with _connect() as conn:
        row = conn.execute(
            "SELECT variant_id, transcript_id, translation_provider, translation_model, "
            "cues_json, created_at FROM translations WHERE variant_id = ?",
            (variant_id,),
        ).fetchone()
    if not row:
        return None
    variant_id, transcript_id, provider, model, cues_json, created_at = row
    return {
        "variant_id": variant_id,
        "transcript_id": transcript_id,
        "translation_provider": provider,
        "translation_model": model,
        "cues": _json_to_cues(cues_json),
        "created_at": created_at,
    }


def put_translation(
    transcript_id: str, provider: str, model: Optional[str], cues: List[Cue]
) -> str:
    variant_id = _variant_id(transcript_id, provider, model)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO translations (variant_id, transcript_id, translation_provider, "
            "translation_model, cues_json, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(variant_id) DO UPDATE SET cues_json=excluded.cues_json, "
            "created_at=excluded.created_at",
            (
                variant_id,
                transcript_id,
                provider,
                model,
                _cues_to_json(cues),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return variant_id


def get_variant(variant_id: str) -> Optional[dict]:
    """A translation joined with its transcript's video info, for a precise download/fetch
    link that doesn't depend on "the most recent variant for this video_id"."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT t.video_id, t.title, t.source_lang, t.source_type, tr.transcript_id, "
            "tr.variant_id, tr.translation_provider, tr.translation_model, tr.cues_json, "
            "tr.created_at "
            "FROM translations tr JOIN transcripts t ON t.transcript_id = tr.transcript_id "
            "WHERE tr.variant_id = ?",
            (variant_id,),
        ).fetchone()
    if not row:
        return None
    (
        video_id,
        title,
        source_lang,
        source_type,
        transcript_id,
        variant_id,
        provider,
        model,
        cues_json,
        created_at,
    ) = row
    return {
        "video_id": video_id,
        "title": title,
        "source_lang": source_lang,
        "source_type": source_type,
        "transcript_id": transcript_id,
        "variant_id": variant_id,
        "translation_provider": provider,
        "translation_model": model,
        "cues": _json_to_cues(cues_json),
        "created_at": created_at,
    }


def list_translations_for_transcript(transcript_id: str) -> List[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT variant_id, translation_provider, translation_model, created_at "
            "FROM translations WHERE transcript_id = ? ORDER BY created_at DESC",
            (transcript_id,),
        ).fetchall()
    return [
        {"variant_id": v, "translation_provider": p, "translation_model": m, "created_at": c}
        for v, p, m, c in rows
    ]


def list_variants(video_id: Optional[str] = None) -> List[dict]:
    """Every cached translation, joined with its transcript, most recent first.

    Powers both the dashboard (all videos, `video_id=None`) and a single video's variant
    picker (`video_id` given). A video with N cached translations produces N rows here.
    """
    query = (
        "SELECT t.video_id, t.title, t.source_lang, t.source_type, tr.transcript_id, "
        "tr.variant_id, tr.translation_provider, tr.translation_model, tr.created_at "
        "FROM translations tr JOIN transcripts t ON t.transcript_id = tr.transcript_id "
    )
    params: tuple = ()
    if video_id:
        query += "WHERE t.video_id = ? "
        params = (video_id,)
    query += "ORDER BY tr.created_at DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "video_id": row_video_id,
            "title": title,
            "source_lang": source_lang,
            "source_type": source_type,
            "transcript_id": transcript_id,
            "variant_id": variant_id,
            "translated": True,  # every row here is a translations join, always translated
            "translation_provider": provider,
            "translation_model": model,
            "created_at": created_at,
        }
        for (
            row_video_id,
            title,
            source_lang,
            source_type,
            transcript_id,
            variant_id,
            provider,
            model,
            created_at,
        ) in rows
    ]
