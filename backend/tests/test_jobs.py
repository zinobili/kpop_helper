import threading
import time

import pytest

from app import jobs


@pytest.fixture(autouse=True)
def isolated_jobs(monkeypatch):
    """Every test gets its own empty job registry, never state leaked from another test."""
    monkeypatch.setattr(jobs, "_jobs", {})


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_create_job_returns_id_and_starts_running(monkeypatch):
    monkeypatch.setattr(jobs.pipeline, "process_video", lambda *a, **k: {"video_id": "v1", "title": "T"})
    job_id = jobs.create_job("https://youtu.be/v1")
    assert job_id
    assert _wait_until(lambda: jobs.get_job(job_id)["status"] == "done")
    job = jobs.get_job(job_id)
    assert job["result"] == {"video_id": "v1", "title": "T"}


def test_get_job_returns_none_for_unknown_id():
    assert jobs.get_job("nope") is None


def test_job_reports_progress_while_running(monkeypatch):
    release = threading.Event()

    def fake_process_video(url, force_refresh, on_progress, force_stt, skip_translation, **_kwargs):
        on_progress("transcribing_audio", None)
        release.wait(timeout=2.0)
        return {"video_id": "v1", "title": "T"}

    monkeypatch.setattr(jobs.pipeline, "process_video", fake_process_video)
    try:
        job_id = jobs.create_job("https://youtu.be/v1")
        assert _wait_until(lambda: jobs.get_job(job_id)["stage"] == "transcribing_audio")
        job = jobs.get_job(job_id)
        assert job["status"] == "running"
    finally:
        release.set()
        _wait_until(lambda: jobs.get_job(job_id)["status"] == "done")


def test_job_records_error_on_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(jobs.pipeline, "process_video", boom)
    job_id = jobs.create_job("https://youtu.be/bad")
    assert _wait_until(lambda: jobs.get_job(job_id)["status"] == "error")
    job = jobs.get_job(job_id)
    assert job["error"] == "kaboom"


def test_list_jobs_most_recent_first(monkeypatch):
    monkeypatch.setattr(jobs.pipeline, "process_video", lambda *a, **k: {"video_id": "v1", "title": "T"})
    first = jobs.create_job("https://youtu.be/first")
    assert _wait_until(lambda: jobs.get_job(first)["status"] == "done")
    second = jobs.create_job("https://youtu.be/second")
    assert _wait_until(lambda: jobs.get_job(second)["status"] == "done")

    summaries = jobs.list_jobs()
    assert [s["job_id"] for s in summaries] == [second, first]
    assert summaries[0]["video_id"] == "v1"
    assert summaries[0]["title"] == "T"


def test_list_jobs_omits_full_result_payload(monkeypatch):
    monkeypatch.setattr(
        jobs.pipeline,
        "process_video",
        lambda *a, **k: {"video_id": "v1", "title": "T", "cues": [{"text_zh": "x"}] * 500},
    )
    job_id = jobs.create_job("https://youtu.be/v1")
    assert _wait_until(lambda: jobs.get_job(job_id)["status"] == "done")

    summary = jobs.list_jobs()[0]
    assert "cues" not in summary
    assert "result" not in summary


def test_on_progress_meta_is_merged_into_job(monkeypatch):
    def fake_process_video(url, force_refresh, on_progress, force_stt, skip_translation, **_kwargs):
        on_progress("translating", "batch 1/2", video_id="v1", title="Real Title")
        return {"video_id": "v1", "title": "Real Title"}

    monkeypatch.setattr(jobs.pipeline, "process_video", fake_process_video)
    job_id = jobs.create_job("https://youtu.be/v1")
    assert _wait_until(lambda: jobs.get_job(job_id)["status"] == "done")

    job = jobs.get_job(job_id)
    assert job["video_id"] == "v1"
    assert job["title"] == "Real Title"


def test_list_jobs_shows_title_for_a_job_that_fails_after_resolving_it(monkeypatch):
    """A job that errors partway through translation should still show the video's title
    (known before the failure), not just fall back to the raw URL."""

    def fake_process_video(url, force_refresh, on_progress, force_stt, skip_translation, **_kwargs):
        on_progress("translating", "batch 1/1", video_id="v1", title="Real Title")
        raise RuntimeError("Translation response missing line numbers: [49]")

    monkeypatch.setattr(jobs.pipeline, "process_video", fake_process_video)
    job_id = jobs.create_job("https://youtu.be/v1")
    assert _wait_until(lambda: jobs.get_job(job_id)["status"] == "error")

    summary = jobs.list_jobs()[0]
    assert summary["status"] == "error"
    assert summary["video_id"] == "v1"
    assert summary["title"] == "Real Title"


def test_list_jobs_falls_back_to_none_when_failure_precedes_resolution(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("yt-dlp exploded before resolving anything")

    monkeypatch.setattr(jobs.pipeline, "process_video", boom)
    job_id = jobs.create_job("https://youtu.be/bad")
    assert _wait_until(lambda: jobs.get_job(job_id)["status"] == "error")

    summary = jobs.list_jobs()[0]
    assert summary["title"] is None
    assert summary["video_id"] is None
