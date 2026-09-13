import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BACKEND_DIR / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRANSLATION_PROVIDER = os.getenv("TRANSLATION_PROVIDER", "gemini")  # "gemini" or "anthropic"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")

DB_PATH = DATA_DIR / "cache.sqlite3"
GLOSSARY_PATH = DATA_DIR / "glossary.json"
AUDIO_DIR = DATA_DIR / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Preference order when picking a caption track to translate from.
CAPTION_LANG_PREFERENCE = ["ko", "en"]
