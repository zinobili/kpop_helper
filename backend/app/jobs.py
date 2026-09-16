import logging
import threading
import uuid
from typing import Dict, Optional

from . import pipeline

logger = logging.getLogger("kpop_helper.jobs")

_jobs: Dict[str, dict] = {}
_lock = threading.Lock()


def _run_job(
    job_id: str,
    url: str,
    force_refresh: bool,
    force_stt: bool,
    skip_translation: bool,
    caption_lang: Optional[str] = None,
    llm_model: Optional[str] = None,
    translation_provider: Optional[str] = None,
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
            caption_lang=caption_lang,
            llm_model=llm_model,
            provider=translation_provider,
        )
        with _lock:
            _jobs[job_id].update(status="done", result=result)
    except Exception as exc:
        logger.exception("Job %s failed (url=%s)", job_id, url)
        with _lock:
            _jobs[job_id].update(status="error", error=str(exc))


def create_job(
    url: str,
    force_refresh: bool = False,
    force_stt: bool = False,
    skip_translation: bool = False,
    caption_lang: Optional[str] = None,
    llm_model: Optional[str] = None,
    translation_provider: Optional[str] = None,
) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"status": "running", "stage": "queued", "url": url}
    thread = threading.Thread(
        target=_run_job,
        args=(
            job_id,
            url,
            force_refresh,
            force_stt,
            skip_translation,
            caption_lang,
            llm_model,
            translation_provider,
        ),
        daemon=True,
    )
    thread.start()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def list_jobs() -> list:
    """Summaries of all jobs known to this process, most recently created first.

    Omits the (potentially large) cues payload from finished jobs - the dashboard only
    needs enough to show what's running and what it last finished.
    """
    with _lock:
        items = list(_jobs.items())
    summaries = []
    for job_id, job in reversed(items):
        result = job.get("result")
        summaries.append(
            {
                "job_id": job_id,
                "url": job.get("url"),
                "status": job.get("status"),
                "stage": job.get("stage"),
                "detail": job.get("detail"),
                "error": job.get("error"),
                "video_id": result.get("video_id") if result else None,
                "title": result.get("title") if result else None,
                "source_lang": result.get("source_lang") if result else None,
                "source_type": result.get("source_type") if result else None,
                "translated": result.get("translated") if result else None,
                "translation_provider": result.get("translation_provider") if result else None,
                "translation_model": result.get("translation_model") if result else None,
                "transcript_id": result.get("transcript_id") if result else None,
                "variant_id": result.get("variant_id") if result else None,
            }
        )
    return summaries
