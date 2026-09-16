import sqlite3

import pytest

from app import cache, config
from app.vtt_utils import Cue


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own empty sqlite file, never the real project data."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "cache.sqlite3")


def _cues():
    return [Cue(start=0.0, end=1.0, text="hi")]


# ---------- transcripts ----------


def test_get_transcript_returns_none_when_not_cached():
    assert cache.get_transcript("missing", "ko", "manual") is None


def test_put_then_get_transcript_round_trips():
    cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cached = cache.get_transcript("abc123", "ko", "manual")
    assert cached["video_id"] == "abc123"
    assert cached["title"] == "Title"
    assert cached["source_lang"] == "ko"
    assert cached["source_type"] == "manual"
    assert cached["cues"][0].text == "hi"
    assert cached["transcript_id"]


def test_transcript_lookup_is_scoped_by_lang_and_type():
    cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    assert cache.get_transcript("abc123", "en", "manual") is None
    assert cache.get_transcript("abc123", "ko", "auto") is None
    assert cache.get_transcript("abc123", "ko", "whisper") is None


def test_put_transcript_is_idempotent_for_same_identity():
    first_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    second_id = cache.put_transcript("abc123", "Title v2", "ko", "manual", _cues())
    assert first_id == second_id
    assert cache.get_transcript("abc123", "ko", "manual")["title"] == "Title v2"


def test_two_source_types_for_same_video_coexist():
    cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cache.put_transcript("abc123", "Title", "ko", "whisper", _cues())
    assert cache.get_transcript("abc123", "ko", "manual") is not None
    assert cache.get_transcript("abc123", "ko", "whisper") is not None


# ---------- translations ----------


def test_get_translation_returns_none_when_not_cached():
    assert cache.get_translation("nope", "gemini", "gemini-3.6-flash") is None


def test_put_then_get_translation_round_trips():
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    cached = cache.get_translation(transcript_id, "gemini", "gemini-3.6-flash")
    assert cached["transcript_id"] == transcript_id
    assert cached["translation_provider"] == "gemini"
    assert cached["translation_model"] == "gemini-3.6-flash"
    assert cached["cues"][0].text == "hi"


def test_translations_for_different_providers_coexist():
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    cache.put_translation(transcript_id, "anthropic", "claude-sonnet-5", _cues())

    assert cache.get_translation(transcript_id, "gemini", "gemini-3.6-flash") is not None
    assert cache.get_translation(transcript_id, "anthropic", "claude-sonnet-5") is not None


def test_put_translation_is_idempotent_for_same_identity():
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    first = cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    second = cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    assert first == second
    assert len(cache.list_translations_for_transcript(transcript_id)) == 1


def test_list_translations_for_transcript_most_recent_first():
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    cache.put_translation(transcript_id, "anthropic", "claude-sonnet-5", _cues())
    variants = cache.list_translations_for_transcript(transcript_id)
    assert {v["translation_provider"] for v in variants} == {"gemini", "anthropic"}


# ---------- get_variant / list_variants ----------


def test_get_variant_returns_none_for_unknown_id():
    assert cache.get_variant("nope") is None


def test_get_variant_joins_transcript_and_translation():
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    variant_id = cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    variant = cache.get_variant(variant_id)
    assert variant["video_id"] == "abc123"
    assert variant["title"] == "Title"
    assert variant["source_lang"] == "ko"
    assert variant["source_type"] == "manual"
    assert variant["translation_provider"] == "gemini"
    assert variant["translation_model"] == "gemini-3.6-flash"


def test_list_variants_empty_when_nothing_cached():
    assert cache.list_variants() == []


def test_list_variants_one_row_per_translation():
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    cache.put_translation(transcript_id, "anthropic", "claude-sonnet-5", _cues())

    other_transcript = cache.put_transcript("xyz789", "Other", "en", "auto", _cues())
    cache.put_translation(other_transcript, "gemini", "gemini-3.6-flash", _cues())

    items = cache.list_variants()
    assert len(items) == 3
    assert {i["video_id"] for i in items} == {"abc123", "xyz789"}


def test_list_variants_filters_by_video_id():
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    other_transcript = cache.put_transcript("xyz789", "Other", "en", "auto", _cues())
    cache.put_translation(other_transcript, "gemini", "gemini-3.6-flash", _cues())

    items = cache.list_variants("abc123")
    assert [i["video_id"] for i in items] == ["abc123"]


def test_list_variants_reports_translated_true():
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", _cues())
    assert cache.list_variants()[0]["translated"] is True


# ---------- legacy migration ----------


def test_migrates_legacy_single_row_cache_into_transcript_and_translation():
    """A DB file from before the multi-variant cache existed (one `subtitles` row per video)
    should get backfilled into transcripts/translations, not lost or errored on."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        "CREATE TABLE subtitles (video_id TEXT PRIMARY KEY, title TEXT, source_lang TEXT, "
        "source_type TEXT, cues_ko_json TEXT, cues_zh_json TEXT, translation_provider TEXT, "
        "translation_model TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO subtitles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "abc123",
            "Title",
            "ko",
            "manual",
            '[{"start": 0.0, "end": 1.0, "text": "hi"}]',
            '[{"start": 0.0, "end": 1.0, "text": "hi"}]',
            "gemini",
            "gemini-3.6-flash",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    transcript = cache.get_transcript("abc123", "ko", "manual")
    assert transcript is not None
    assert transcript["title"] == "Title"
    variants = cache.list_variants("abc123")
    assert len(variants) == 1
    assert variants[0]["translation_provider"] == "gemini"
    assert variants[0]["translation_model"] == "gemini-3.6-flash"


def test_migrates_legacy_row_missing_translation_provider():
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        "CREATE TABLE subtitles (video_id TEXT PRIMARY KEY, title TEXT, source_lang TEXT, "
        "source_type TEXT, cues_ko_json TEXT, cues_zh_json TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO subtitles VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "abc123",
            "Title",
            "ko",
            "manual",
            '[{"start": 0.0, "end": 1.0, "text": "hi"}]',
            '[{"start": 0.0, "end": 1.0, "text": "hi"}]',
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    variants = cache.list_variants("abc123")
    assert len(variants) == 1
    assert variants[0]["translation_provider"] == "unknown"
