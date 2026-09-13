from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cache, glossary, jobs
from .srt_utils import cues_to_srt
from .vtt_utils import Cue

app = FastAPI(title="kpop_helper backend")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ProcessRequest(BaseModel):
    url: str
    force_refresh: bool = False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process")
def process(req: ProcessRequest):
    job_id = jobs.create_job(req.url, force_refresh=req.force_refresh)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id.")
    return job


@app.get("/subtitles/{video_id}.srt", response_class=PlainTextResponse)
def get_srt(video_id: str):
    cached = cache.get(video_id)
    if not cached:
        raise HTTPException(status_code=404, detail="No cached subtitles for this video_id yet.")
    cues = [Cue(start=c.start, end=c.end, text=c.text) for c in cached["cues_zh"]]
    return cues_to_srt(cues)


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
