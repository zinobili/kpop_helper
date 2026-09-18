import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

CREATE TABLE IF NOT EXISTS translation_batches (
    batch_key TEXT PRIMARY KEY,
    transcript_id TEXT NOT NULL,
    translation_provider TEXT NOT NULL,
    translation_model TEXT NOT NULL,
    translations_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transcripts_video ON transcripts(video_id);
CREATE INDEX IF NOT EXISTS idx_translations_transcript ON translations(transcript_id);
CREATE INDEX IF NOT EXISTS idx_batches_variant
    ON translation_batches(transcript_id, translation_provider, translation_model);
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


# ---------- in-progress translation batches ----------
#
# Finished batches of a translation that hasn't completed yet, so a job that dies partway (quota
# exhausted, a batch out of retries, server restart) can resume instead of re-paying for the
# batches that already succeeded. A row's key hashes the exact LLM request (see
# translate._batch_prompt_hash) together with provider/model, so a changed glossary, prompt,
# batch size or model just misses instead of reusing a stale result. Rows are deleted once the
# full translation is stored.

_STALE_BATCH_DAYS = 14


def _batch_key(transcript_id: str, provider: str, model: Optional[str], prompt_hash: str) -> str:
    raw = f"{transcript_id}|{provider}|{model or ''}|{prompt_hash}"
    return hashlib.sha1(raw.encode()).hexdigest()


def get_batch(
    transcript_id: str, provider: str, model: Optional[str], prompt_hash: str
) -> Optional[List[str]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT translations_json FROM translation_batches WHERE batch_key = ?",
            (_batch_key(transcript_id, provider, model, prompt_hash),),
        ).fetchone()
    if not row:
        return None
    try:
        translations = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(translations, list) or not all(isinstance(t, str) for t in translations):
        return None
    return translations


def put_batch(
    transcript_id: str,
    provider: str,
    model: Optional[str],
    prompt_hash: str,
    translations: List[str],
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO translation_batches (batch_key, transcript_id, translation_provider, "
            "translation_model, translations_json, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(batch_key) DO UPDATE SET translations_json=excluded.translations_json, "
            "created_at=excluded.created_at",
            (
                _batch_key(transcript_id, provider, model, prompt_hash),
                transcript_id,
                provider,
                model or "",
                json.dumps(translations, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def clear_batches(transcript_id: str, provider: str, model: Optional[str]) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM translation_batches WHERE transcript_id = ? AND translation_provider = ? "
            "AND translation_model = ?",
            (transcript_id, provider, model or ""),
        )
    return cursor.rowcount


def purge_stale_batches(max_age_days: int = _STALE_BATCH_DAYS) -> int:
    """Drops checkpoints from jobs that failed and were never retried."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM translation_batches WHERE created_at < ?", (cutoff,))
    return cursor.rowcount


class BatchCheckpoint:
    """One translation's (transcript, provider, model) view of the in-progress batch store -
    what translate.translate_cues talks to, so translate.py needn't know about the DB."""

    def __init__(self, transcript_id: str, provider: str, model: Optional[str]):
        self.transcript_id = transcript_id
        self.provider = provider
        self.model = model
        self.resumed = 0

    def load(self, prompt_hash: str) -> Optional[List[str]]:
        translations = get_batch(self.transcript_id, self.provider, self.model, prompt_hash)
        if translations is not None:
            self.resumed += 1
        return translations

    def save(self, prompt_hash: str, translations: List[str]) -> None:
        put_batch(self.transcript_id, self.provider, self.model, prompt_hash, translations)

    def clear(self) -> int:
        return clear_batches(self.transcript_id, self.provider, self.model)


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
