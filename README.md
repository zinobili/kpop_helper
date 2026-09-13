# kpop_helper

Watch K-pop YouTube videos with Traditional Chinese subtitles, translated with an LLM for
fandom-aware nuance, instead of relying on Korean audio or English captions.

## Status

**Stage 1 (current)**: backend pipeline only, usable via CLI or a local API.
Later stages: web app UI, Chrome extension overlay, live-stream support.

## How it works

1. Fetches the video's existing YouTube captions (prefers Korean, falls back to English;
   manual captions preferred over auto-generated).
2. If a video has no captions at all, transcribes the audio locally with Whisper.
3. Translates the Korean/English text into Traditional Chinese using an LLM (Gemini or
   Claude, see below), applying an editable glossary (`backend/data/glossary.json`) so idol
   names, group names, and fandom slang stay consistent across videos.
4. Caches the result (SQLite) so re-processing the same video is instant and free.

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Edit `backend/.env` to choose a translation provider:

- **`TRANSLATION_PROVIDER=gemini`** (default, good for a free POC): set `GEMINI_API_KEY` to a
  free key from https://aistudio.google.com/apikey. Free-tier requests may be used by Google
  to improve their models — fine for casual show captions, worth knowing.
- **`TRANSLATION_PROVIDER=anthropic`** (paid, generally higher translation nuance): set
  `ANTHROPIC_API_KEY` to your own key from https://console.anthropic.com/. See
  [cost notes](#cost) below.

Only the key for the provider you selected is required.

## Test on the CLI

```bash
cd backend
.venv\Scripts\python cli.py "https://www.youtube.com/watch?v=yLGXM5O8v5Q"
```

This writes a `<video_id>.srt` file with Traditional Chinese subtitles. Open the video in
VLC (or download it for personal testing with `yt-dlp`) and load the `.srt` as an external
subtitle track to check timing and translation quality.

## Test via the local API

```bash
cd backend
.venv\Scripts\uvicorn app.main:app --reload
```

Then, e.g. with `curl`:

```bash
curl -X POST http://127.0.0.1:8000/process -H "Content-Type: application/json" \
  -d "{\"url\": \"https://www.youtube.com/watch?v=yLGXM5O8v5Q\"}"

curl http://127.0.0.1:8000/subtitles/yLGXM5O8v5Q.srt
```

Glossary management:

```bash
curl -X POST http://127.0.0.1:8000/glossary -H "Content-Type: application/json" \
  -d "{\"term\": \"오빌리\", \"translation\": \"OB\", \"notes\": \"stage name, keep as-is\"}"

curl http://127.0.0.1:8000/glossary
```

## Cost

- **Gemini**: free tier, rate-limited (requests/minute and a daily quota — check current limits
  at https://ai.google.dev/pricing before processing many videos back-to-back).
- **Anthropic** (`claude-sonnet-5`): pay-as-you-go, $2/1M input tokens + $10/1M output tokens.
  A ~1-hour dialogue-heavy episode (~500 caption lines) costs roughly $0.10-0.15; a short MV
  with sparse lyrics costs a fraction of a cent. Charged once per video — cached re-processing
  is free.
- Everything else (caption fetch, Whisper fallback, caching) runs locally and is free.
