import logging
from pathlib import Path
from typing import Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cache, config, glossary, jobs, pipeline
from .srt_utils import cues_to_srt

def _configure_logging() -> None:
    # uvicorn only configures its own loggers, so without this the kpop_helper.* INFO lines
    # (LLM token usage, retries) are silently dropped by Python's WARNING-level default.
    pkg_logger = logging.getLogger("kpop_helper")
    if pkg_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(config.LOG_LEVEL)


_configure_logging()

app = FastAPI(title="kpop_helper backend")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ProcessRequest(BaseModel):
    url: str
    force_refresh: bool = False
    force_stt: bool = False  # ignore existing YouTube captions, always transcribe via Whisper
    skip_translation: bool = False  # output the original-language transcript, no LLM call
    caption_lang: Optional[Literal["ko", "en"]] = None  # None = auto (Korean preferred)
    llm_model: Optional[str] = None  # only used when translation_provider resolves to "local"
    translation_provider: Optional[Literal["gemini", "anthropic", "deepseek", "local"]] = None
    # ^ None = use the .env default (TRANSLATION_PROVIDER)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process")
def process(req: ProcessRequest):
    job_id = jobs.create_job(
        req.url,
        force_refresh=req.force_refresh,
        force_stt=req.force_stt,
        skip_translation=req.skip_translation,
        caption_lang=req.caption_lang,
        llm_model=req.llm_model,
        translation_provider=req.translation_provider,
    )
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id.")
    return job


@app.get("/local-models")
def local_models():
    """Models currently available on a local LLM server (Ollama, LM Studio, etc), for the
    frontend's translation-provider/model dropdowns.

    Always attempts the fetch, regardless of the .env default TRANSLATION_PROVIDER - a request
    can pick "local" per-call via ProcessRequest.translation_provider, so the dropdown needs to
    work even when a different provider is the default. default_provider tells the frontend
    which option to show as "the current default".
    """
    models: list = []
    try:
        resp = httpx.get(f"{config.LOCAL_LLM_BASE_URL}/models", timeout=5)
        resp.raise_for_status()
        models = [m["id"] for m in resp.json().get("data", [])]
    except Exception:
        pass  # server not running / unreachable - frontend just shows an empty dropdown
    return {"default_provider": config.TRANSLATION_PROVIDER, "models": models}


@app.get("/dashboard")
def dashboard():
    return {
        "processed": cache.list_variants(),
        "jobs": jobs.list_jobs(),
    }


@app.get("/videos/{video_id}/variants")
def video_variants(video_id: str):
    """Every cached translation for one video, for a variant picker - so a client can offer
    an already-translated version instead of re-submitting to /process."""
    return cache.list_variants(video_id)


@app.get("/translate-preview")
def translate_preview(
    url: str,
    caption_lang: Optional[Literal["ko", "en"]] = None,
    force_stt: bool = False,
    translation_provider: Optional[Literal["gemini", "anthropic", "deepseek", "local"]] = None,
    llm_model: Optional[str] = None,
):
    """What a /process call with these settings would do, without doing any of the expensive
    work: which transcript it would resolve to, whether a translation already matches these
    exact settings, and what other cached translations exist for that same transcript. Lets a
    client prompt "use this existing translation?" before kicking off a real job."""
    try:
        return pipeline.preview_translation_options(
            url, caption_lang=caption_lang, force_stt=force_stt,
            provider=translation_provider, llm_model=llm_model,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/variant/{variant_id}")
def get_variant(variant_id: str):
    result = pipeline.get_variant_result(variant_id)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown variant_id.")
    return result


@app.get("/subtitles/variant/{variant_id}.srt", response_class=PlainTextResponse)
def get_variant_srt(variant_id: str):
    variant = cache.get_variant(variant_id)
    if not variant:
        raise HTTPException(status_code=404, detail="Unknown variant_id.")
    return cues_to_srt(variant["cues"])


@app.get("/subtitles/{video_id}.srt", response_class=PlainTextResponse)
def get_srt(video_id: str):
    variants = cache.list_variants(video_id)
    if not variants:
        raise HTTPException(status_code=404, detail="No cached subtitles for this video_id yet.")
    variant = cache.get_variant(variants[0]["variant_id"])  # most recently created
    return cues_to_srt(variant["cues"])


@app.get("/glossary")
def list_glossary():
    return [e.model_dump() for e in glossary.list_entries()]


@app.post("/glossary")
def upsert_glossary(entry: glossary.GlossaryEntry):
    glossary.upsert_entry(entry)
    return {"status": "ok"}


@app.delete("/glossary/{term}")
def delete_glossary(term: str):
    if not glossary.delete_entry(term):
        raise HTTPException(status_code=404, detail="Term not found.")
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
