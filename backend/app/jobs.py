import logging
import threading
import uuid
from typing import Dict, Optional

from . import pipeline

logger = logging.getLogger("kpop_helper.jobs")

_jobs: Dict[str, dict] = {}
_lock = threading.Lock()


def _run_job(
    job_id: str, url: str, force_refresh: bool, force_stt: bool, skip_translation: bool
) -> None:
    def on_progress(stage: str, detail: Optional[str] = None) -> None:
        with _lock:
            _jobs[job_id]["stage"] = stage
            _jobs[job_id]["detail"] = detail

    try:
        result = pipeline.process_video(
            url,
            force_refresh=force_refresh,
            on_progress=on_progress,
            force_stt=force_stt,
            skip_translation=skip_translation,
        )
        with _lock:
            _jobs[job_id].update(status="done", result=result)
    except Exception as exc:
        logger.exception("Job %s failed (url=%s)", job_id, url)
        with _lock:
            _jobs[job_id].update(status="error", error=str(exc))


def create_job(
    url: str, force_refresh: bool = False, force_stt: bool = False, skip_translation: bool = False
) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"status": "running", "stage": "queued", "url": url}
    thread = threading.Thread(
        target=_run_job, args=(job_id, url, force_refresh, force_stt, skip_translation), daemon=True
    )
    thread.start()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
