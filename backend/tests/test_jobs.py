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
