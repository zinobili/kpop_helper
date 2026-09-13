# kpop_helper

Watch K-pop YouTube videos with Traditional Chinese subtitles, translated with an LLM for
fandom-aware nuance, instead of relying on Korean audio or English captions.

## Status

**Stage 1**: backend pipeline, usable via CLI or a local API. **Done.**
**Stage 2**: local web app - paste a URL, watch with a synced subtitle overlay,
or download the `.srt`. **Done.**
**Stage 3 (current)**: Chrome extension - overlay directly on youtube.com while watching
normally. **Done.**
Later stages: live-stream support.

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

## Test the web app

```bash
cd backend
.venv\Scripts\uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 in a browser:

1. Paste a YouTube URL and click **Load**. Status updates show the current stage (fetching
   captions / transcribing / translating) - translating ~500 lines can take a minute or two
   on Gemini's free tier.
2. Once done, the video plays inline with a Traditional Chinese subtitle overlay synced to
   playback (if YouTube's own captions also appear, click the video's **CC** button to turn
   them off so they don't clash with the overlay).
3. Use **Download .srt** to get the subtitle file for any other player (e.g. VLC on a
   downloaded copy of the video).
4. The **Glossary** panel lets you add/remove terms (idol names, group names, fandom slang)
   that future translations will use for consistency.

Re-loading the same URL is instant (served from cache) unless you check **Force re-process**.

## Test on the CLI

```bash
cd backend
.venv\Scripts\python cli.py "https://www.youtube.com/watch?v=yLGXM5O8v5Q"
```

This writes a `<video_id>.srt` file with Traditional Chinese subtitles directly, without
starting the web server.

## Test the Chrome extension

The backend must be running first (`.venv\Scripts\uvicorn app.main:app --reload` from `backend/`).

1. Open `chrome://extensions` in Chrome, enable **Developer mode** (top-right toggle).
2. Click **Load unpacked** and select the `extension/` folder.
3. Go to any YouTube video (e.g. `https://www.youtube.com/watch?v=yLGXM5O8v5Q`). A small
   **翻譯** (Translate) button appears in the top-right corner of the player.
4. Click it - status text shows progress (fetching/transcribing/translating), then the
   button becomes **隱藏字幕** (Hide subtitles) and the overlay starts syncing with playback.
   If YouTube's native captions also appear, click the video's own **CC** button to turn
   them off.
5. Click the extension's toolbar icon to change the backend URL if it's not running on the
   default `http://127.0.0.1:8000` (e.g. if you later host the backend elsewhere).

The extension talks to the backend from its background service worker (not the page itself),
so no CORS configuration is needed on the backend.

## API reference

```bash
# Start a translation job (returns a job_id immediately; processing runs in the background)
curl -X POST http://127.0.0.1:8000/process -H "Content-Type: application/json" \
  -d "{\"url\": \"https://www.youtube.com/watch?v=yLGXM5O8v5Q\"}"

# Poll job status/result
curl http://127.0.0.1:8000/jobs/<job_id>

# Once done, fetch the cached subtitle file directly
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
