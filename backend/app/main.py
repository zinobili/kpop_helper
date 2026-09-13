from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from . import cache, glossary, pipeline
from .srt_utils import cues_to_srt
from .vtt_utils import Cue

app = FastAPI(title="kpop_helper backend")


class ProcessRequest(BaseModel):
    url: str
    force_refresh: bool = False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process")
def process(req: ProcessRequest):
    try:
        return pipeline.process_video(req.url, force_refresh=req.force_refresh)
    except Exception as exc:  # surfaced to the caller for now; stage 1 is local-only
        raise HTTPException(status_code=500, detail=str(exc))


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
