# Backlog

## To do

- [ ] **Export YouTube cookies for yt-dlp.** YouTube periodically walls off `yt-dlp` with
  "Sign in to confirm you're not a bot" on any video not already cached - it's an anti-bot
  measure on YouTube's side, not a bug tied to a specific video.
  1. Install a "cookies.txt" export extension for your browser (e.g. search your browser's
     extension store for "Get cookies.txt LOCALLY" - use one with source available/many
     installs, since it can read your session cookies).
  2. Go to https://www.youtube.com while logged in, click the extension, export cookies for
     this site, and save the file as `backend/data/cookies.txt`.
  3. Nothing else to configure - `backend/.env` already points `YTDLP_COOKIES_FILE` at that
     path, so it's picked up automatically once the file exists.

  Treat that file like a password - it's a snapshot of your logged-in session
  (`backend/data/` is gitignored, so it won't get committed). Re-export it if it stops
  working (cookies expire).

- [x] Hovering on a Chrome extension message should show the full message (useful for
  displaying full error text).

- [x] Add a dashboard endpoint showing which audio files have been processed.
  - [x] it should be a table of showing name, status, approach, used_transcription, used_translation, model_used

- [x] While processing, show what stage it's currently at (e.g. on the dashboard above).

- [x] switching of llm model from localhost endpoint instead of env variable (dropdown)

- [x] option to choose whether to translate from youtube english subtitle or from korean subtitle

- [x] Cache multiple translation variants per video instead of one row per video_id, so
  translating with a different LLM or a different caption language keeps prior results instead
  of overwriting them. Cache is now split into `transcripts` (keyed by video_id + caption
  language + source: manual/auto/whisper) and `translations` (keyed by transcript + provider +
  model), with a `variant_id` per translation. The Chrome extension has a caption-language
  selector (no LLM picker) and prompts "use this existing translation?" via a new
  `/translate-preview` endpoint when a different-provider translation already exists for the
  requested language, before falling back to a fresh translate. The web dashboard now lists one
  row per variant. Migration from the old single-row-per-video cache runs automatically on
  first use of an existing DB.


