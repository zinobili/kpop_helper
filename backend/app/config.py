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

TRANSLATION_PROVIDER = os.getenv("TRANSLATION_PROVIDER", "gemini")  # "gemini", "anthropic", "deepseek", "local", or "claude_agent"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# For TRANSLATION_PROVIDER=claude_agent: drives the Claude Code CLI (via the Claude Agent SDK) as
# a local subprocess instead of calling the Anthropic API directly. Auth is whatever the CLI
# itself is configured with on this machine - run `claude login` once to use a Claude
# subscription instead of metered API billing - not ANTHROPIC_API_KEY above, which is only read
# by the "anthropic" provider. Leave unset to let the CLI use its own default model.
CLAUDE_AGENT_MODEL = os.getenv("CLAUDE_AGENT_MODEL", "")

# The SDK finds the `claude` binary via shutil.which() on the process's own PATH by default -
# fine for an interactive shell, but a service/system account (e.g. running the backend as a
# Windows service or systemd unit) often has a different PATH that doesn't include wherever
# `npm install -g` put it. Set this to the CLI's full path (e.g. output of `where claude` /
# `which claude`) to bypass the PATH search entirely. Leave unset to keep using PATH lookup.
CLAUDE_AGENT_CLI_PATH = os.getenv("CLAUDE_AGENT_CLI_PATH", "")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_MAX_RETRIES = int(os.getenv("DEEPSEEK_MAX_RETRIES", "5"))
# "disabled" or "enabled". deepseek-flash/deepseek-v4-pro think by default, and the reasoning
# tokens are billed as output - for subtitle translation that was ~96% of output tokens.
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower()
# The API rejects anything else with a 422, so map common on/off spellings to its values.
DEEPSEEK_THINKING = {"off": "disabled", "false": "disabled", "no": "disabled", "0": "disabled",
                     "on": "enabled", "true": "enabled", "yes": "enabled", "1": "enabled"}.get(
    DEEPSEEK_THINKING, DEEPSEEK_THINKING
)

# For TRANSLATION_PROVIDER=local: any OpenAI-compatible chat-completions server running on
# your machine (Ollama, LM Studio, llama.cpp server, etc). Unlike the other providers, the
# model isn't fixed via env var - the frontend fetches whatever models that server currently
# has loaded (GET {LOCAL_LLM_BASE_URL}/models) and lets you pick one from a dropdown per
# request. LOCAL_LLM_MODEL is only a fallback for API callers that don't pass one explicitly.
LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# Free-tier Gemini Flash is rate-limited to ~5 requests/minute per project. Throttle client-side
# to that (rather than just reacting to 429s) so a long video's batches don't burn through the
# quota in the first 20 seconds. Raise this if you're on a paid Gemini tier with a higher cap.
GEMINI_RPM = int(os.getenv("GEMINI_RPM", "5"))
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "5"))

# How many subtitle lines to translate per LLM call. Free-tier Gemini also caps total
# *requests per day* (as low as 20/day for some models) on top of the RPM limit - that cap
# can't be worked around by pacing, only by using fewer, larger requests. 200 keeps a
# ~500-line video to ~3 requests instead of ~13. Lower this if large batches cause the model
# to drop/misorder lines; the paid Anthropic path has no such pressure to batch this large.
# DeepSeek has no daily-quota pressure either, and empirically drops a line near the end of a
# 200-line batch often enough to matter - default it smaller unless the user overrides. Local
# models are typically weaker still, so default them small too.
_DEFAULT_TRANSLATE_BATCH_SIZE = "50" if TRANSLATION_PROVIDER in ("deepseek", "local") else "200"
TRANSLATE_BATCH_SIZE = int(os.getenv("TRANSLATE_BATCH_SIZE", _DEFAULT_TRANSLATE_BATCH_SIZE))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

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
