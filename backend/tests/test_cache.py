import pytest

from app import cache, config
from app.vtt_utils import Cue


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own empty sqlite file, never the real project data."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "cache.sqlite3")


def _cues():
    return [Cue(start=0.0, end=1.0, text="hi")]


def test_get_returns_none_when_not_cached():
    assert cache.get("missing") is None


def test_put_then_get_round_trips():
    cache.put("abc123", "Title", "ko", "captions", _cues(), _cues())
    cached = cache.get("abc123")
    assert cached["video_id"] == "abc123"
    assert cached["title"] == "Title"
    assert cached["source_lang"] == "ko"
    assert cached["source_type"] == "captions"
    assert cached["cues_ko"][0].text == "hi"


def test_list_all_empty_when_nothing_cached():
    assert cache.list_all() == []


def test_list_all_reflects_processed_videos_most_recent_first():
    cache.put("first", "First", "ko", "captions", _cues(), _cues())
    cache.put("second", "Second", "en", "whisper", _cues(), _cues())
    items = cache.list_all()
    assert [i["video_id"] for i in items] == ["second", "first"]
    assert items[0]["title"] == "Second"
    assert items[0]["source_type"] == "whisper"
    assert items[0]["created_at"]


def test_list_all_omits_cue_payload():
    cache.put("abc123", "Title", "ko", "captions", _cues(), _cues())
    item = cache.list_all()[0]
    assert "cues_ko" not in item
    assert "cues_zh" not in item


def test_put_then_get_round_trips_translation_provider_and_model():
    cache.put(
        "abc123", "Title", "ko", "captions", _cues(), _cues(),
        translation_provider="gemini", translation_model="gemini-3.6-flash",
    )
    cached = cache.get("abc123")
    assert cached["translation_provider"] == "gemini"
    assert cached["translation_model"] == "gemini-3.6-flash"


def test_list_all_reports_every_row_as_translated():
    cache.put("abc123", "Title", "ko", "captions", _cues(), _cues())
    item = cache.list_all()[0]
    assert item["translated"] is True
    assert item["translation_provider"] is None
    assert item["translation_model"] is None
