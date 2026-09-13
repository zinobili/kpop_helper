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
