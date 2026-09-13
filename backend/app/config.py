import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent.parent

# A relative DATA_DIR (the default, and what .env.example ships) must anchor to
# BACKEND_DIR rather than the process's current working directory - otherwise running
# uvicorn from a different cwd (e.g. via `--app-dir`) silently reads/writes a second,
# empty data directory instead of the one the CLI uses.
_data_dir = Path(os.getenv("DATA_DIR", "./data"))
if not _data_dir.is_absolute():
    _data_dir = BACKEND_DIR / _data_dir
DATA_DIR = _data_dir.resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRANSLATION_PROVIDER = os.getenv("TRANSLATION_PROVIDER", "gemini")  # "gemini" or "anthropic"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# Free-tier Gemini Flash is rate-limited to ~5 requests/minute per project. Throttle client-side
# to that (rather than just reacting to 429s) so a long video's batches don't burn through the
# quota in the first 20 seconds. Raise this if you're on a paid Gemini tier with a higher cap.
GEMINI_RPM = int(os.getenv("GEMINI_RPM", "5"))
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "5"))

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")

# YouTube periodically walls off yt-dlp with a "Sign in to confirm you're not a bot" error.
# Two ways to clear it, in preference order (a cookies file wins if both are set):
#  - YTDLP_COOKIES_FILE: path to a Netscape-format cookies.txt exported from a logged-in
#    browser session. Works even while the browser stays open (needed here, since you'll
#    have it open to use the extension) - unlike cookiesfrombrowser below, which reads the
#    browser's live cookie database and fails with a lock error while that browser is running.
#  - YTDLP_COOKIES_FROM_BROWSER: a browser name (chrome, edge, firefox, brave, ...) - simpler,
#    but only reliable when that browser is fully closed while the backend runs.
# Leave both empty to disable (yt-dlp then makes unauthenticated requests, as before).
YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "")
YTDLP_COOKIES_FROM_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "")

DB_PATH = DATA_DIR / "cache.sqlite3"
GLOSSARY_PATH = DATA_DIR / "glossary.json"
AUDIO_DIR = DATA_DIR / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Preference order when picking a caption track to translate from.
CAPTION_LANG_PREFERENCE = ["ko", "en"]
