import pytest
from fastapi.testclient import TestClient

from app import cache, config, jobs, main
from app.vtt_utils import Cue


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Every test gets its own empty DB and job registry, never the real project data."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "cache.sqlite3")
    monkeypatch.setattr(jobs, "_jobs", {})


@pytest.fixture
def client():
    return TestClient(main.app)


def _cues():
    return [Cue(start=0.0, end=1.0, text="hi")]


def _seed_variant(video_id="abc123", title="Title", source_lang="ko", source_type="manual",
                   provider="gemini", model="gemini-3.6-flash"):
    transcript_id = cache.put_transcript(video_id, title, source_lang, source_type, _cues())
    variant_id = cache.put_translation(transcript_id, provider, model, _cues())
    return transcript_id, variant_id


def test_process_passes_new_fields_to_create_job(client, monkeypatch):
    captured = {}

    def fake_create_job(
        url, force_refresh=False, force_stt=False, skip_translation=False,
        caption_lang=None, llm_model=None, translation_provider=None,
    ):
        captured.update(
            caption_lang=caption_lang, llm_model=llm_model, translation_provider=translation_provider
        )
        return "job123"

    monkeypatch.setattr(main.jobs, "create_job", fake_create_job)

    resp = client.post(
        "/process",
        json={
            "url": "https://youtu.be/v1",
            "caption_lang": "en",
            "llm_model": "llama3",
            "translation_provider": "local",
        },
    )

    assert resp.json() == {"job_id": "job123"}
    assert captured == {"caption_lang": "en", "llm_model": "llama3", "translation_provider": "local"}


def test_local_models_reports_the_env_default_provider(client, monkeypatch):
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(
        main.httpx, "get", lambda url, timeout=5: (_ for _ in ()).throw(ConnectionError("refused"))
    )
    resp = client.get("/local-models")
    assert resp.json() == {"default_provider": "gemini", "models": []}


def test_local_models_lists_models_regardless_of_default_provider(client, monkeypatch):
    # A request can pick provider="local" per-call even when it isn't the .env default, so
    # this must not gate on config.TRANSLATION_PROVIDER.
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "deepseek")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "llama3"}, {"id": "mistral"}]}

    monkeypatch.setattr(main.httpx, "get", lambda url, timeout=5: FakeResponse())

    resp = client.get("/local-models")
    assert resp.json() == {"default_provider": "deepseek", "models": ["llama3", "mistral"]}


def test_local_models_returns_empty_list_when_server_unreachable(client, monkeypatch):
    def fake_get(url, timeout=5):
        raise ConnectionError("refused")

    monkeypatch.setattr(main.httpx, "get", fake_get)

    resp = client.get("/local-models")
    assert resp.json()["models"] == []


def test_dashboard_empty(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert resp.json() == {"processed": [], "jobs": []}


def test_dashboard_lists_processed_videos(client):
    _seed_variant()

    data = client.get("/dashboard").json()
    assert len(data["processed"]) == 1
    assert data["processed"][0]["video_id"] == "abc123"
    assert data["processed"][0]["title"] == "Title"


def test_dashboard_lists_one_row_per_variant(client):
    _seed_variant(provider="gemini")
    transcript_id = cache.get_transcript("abc123", "ko", "manual")["transcript_id"]
    cache.put_translation(transcript_id, "anthropic", "claude-sonnet-5", _cues())

    data = client.get("/dashboard").json()
    assert len(data["processed"]) == 2


def test_dashboard_lists_running_job_stage(client, monkeypatch):
    monkeypatch.setattr(
        jobs.pipeline,
        "process_video",
        lambda url, force_refresh, on_progress, force_stt, skip_translation, **_kwargs: (
            on_progress("translating", "batch 1/2"),
            {"video_id": "v1", "title": "T"},
        )[1],
    )
    job_id = jobs.create_job("https://youtu.be/v1")

    # Poll until the job shows up as either running or already finished - both are
    # valid outcomes of the race between this request and the background thread.
    import time

    deadline = time.monotonic() + 2.0
    job_summary = None
    while time.monotonic() < deadline:
        data = client.get("/dashboard").json()
        matches = [j for j in data["jobs"] if j["job_id"] == job_id]
        if matches:
            job_summary = matches[0]
            if job_summary["status"] == "done":
                break
        time.sleep(0.01)

    assert job_summary is not None
    assert job_summary["url"] == "https://youtu.be/v1"


def test_video_variants_lists_only_that_video(client):
    _seed_variant(video_id="abc123")
    _seed_variant(video_id="xyz789")

    resp = client.get("/videos/abc123/variants")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["video_id"] == "abc123"


def test_video_variants_empty_for_unknown_video(client):
    resp = client.get("/videos/unknown/variants")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_variant_returns_cues(client):
    _transcript_id, variant_id = _seed_variant()
    resp = client.get(f"/variant/{variant_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["video_id"] == "abc123"
    assert data["cues"][0]["text_zh"] == "hi"


def test_get_variant_404_for_unknown_id(client):
    resp = client.get("/variant/nope")
    assert resp.status_code == 404


def test_variant_srt_download(client):
    _transcript_id, variant_id = _seed_variant()
    resp = client.get(f"/subtitles/variant/{variant_id}.srt")
    assert resp.status_code == 200
    assert "hi" in resp.text


def test_variant_srt_404_for_unknown_id(client):
    resp = client.get("/subtitles/variant/nope.srt")
    assert resp.status_code == 404


def test_legacy_video_srt_resolves_most_recent_variant(client):
    _seed_variant()
    resp = client.get("/subtitles/abc123.srt")
    assert resp.status_code == 200
    assert "hi" in resp.text


def test_legacy_video_srt_404_when_nothing_cached(client):
    resp = client.get("/subtitles/nope.srt")
    assert resp.status_code == 404


def test_translate_preview_no_cache(client, monkeypatch):
    monkeypatch.setattr(
        main.pipeline, "resolve_transcript_identity",
        lambda url, caption_lang=None, force_stt=False: (
            {"video_id": "abc123", "title": "Title", "source_lang": "ko", "source_type": "manual"}
        ),
    )
    resp = client.get("/translate-preview", params={"url": "https://youtu.be/abc123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exact_match"] is False
    assert data["alternates"] == []


def test_translate_preview_reports_alternates(client, monkeypatch):
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(
        main.pipeline, "resolve_transcript_identity",
        lambda url, caption_lang=None, force_stt=False: (
            {"video_id": "abc123", "title": "Title", "source_lang": "ko", "source_type": "manual"}
        ),
    )
    transcript_id = cache.put_transcript("abc123", "Title", "ko", "manual", _cues())
    cache.put_translation(transcript_id, "anthropic", "claude-sonnet-5", _cues())

    resp = client.get("/translate-preview", params={"url": "https://youtu.be/abc123"})
    data = resp.json()
    assert data["exact_match"] is False
    assert len(data["alternates"]) == 1
    assert data["alternates"][0]["translation_provider"] == "anthropic"


def test_translate_preview_400_on_error(client, monkeypatch):
    def boom(url, caption_lang=None, force_stt=False):
        raise RuntimeError("yt-dlp exploded")

    monkeypatch.setattr(main.pipeline, "resolve_transcript_identity", boom)
    resp = client.get("/translate-preview", params={"url": "https://youtu.be/abc123"})
    assert resp.status_code == 400
