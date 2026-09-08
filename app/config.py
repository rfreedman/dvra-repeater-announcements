from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"
VOICES_DIR = Path(os.environ.get("TTS_VOICES_DIR", ROOT_DIR / "voices")).expanduser()
PIPER_VOICES_DIR = VOICES_DIR / "piper"

DEFAULT_PIPER_VOICE = os.environ.get("TTS_DEFAULT_VOICE", "en_US-ryan-medium")

HOST = os.environ.get("TTS_HOST", "0.0.0.0")
PORT = int(os.environ.get("TTS_PORT", "8000"))
MAX_TEXT_CHARS = int(os.environ.get("TTS_MAX_TEXT_CHARS", "5000"))
DEFAULT_SPEED = float(os.environ.get("TTS_SPEED", "1.0"))
DEFAULT_SENTENCE_PAUSE = float(os.environ.get("TTS_SENTENCE_PAUSE", "0.25"))

DATA_DIR = Path(os.environ.get("TTS_DATA_DIR", ROOT_DIR / "data")).expanduser()
ANNOUNCEMENTS_PATH = DATA_DIR / "announcements.json"
LOG_DIR = Path(os.environ.get("TTS_LOG_DIR", DATA_DIR / "logs")).expanduser()
LOG_KEEP_DAYS = int(os.environ.get("TTS_LOG_KEEP_DAYS", "30"))
TIMEZONE = os.environ.get("TTS_TIMEZONE", "America/New_York")
PTT_LEAD_SECONDS = float(os.environ.get("TTS_PTT_LEAD_SECONDS", "0.4"))
DEFAULT_BUSY_RETRY_SECONDS = float(os.environ.get("TTS_BUSY_RETRY_SECONDS", "5"))
DEFAULT_BUSY_GIVE_UP_SECONDS = float(os.environ.get("TTS_BUSY_GIVE_UP_SECONDS", "45"))
SCHEDULER_MAX_WAIT_SECONDS = float(os.environ.get("TTS_SCHEDULER_MAX_WAIT_SECONDS", "5"))
DEFAULT_SLOT_HALF_WINDOW_MINUTES = int(os.environ.get("TTS_SLOT_HALF_WINDOW_MINUTES", "10"))
LOOKAHEAD_HOURS = int(os.environ.get("TTS_SLOT_LOOKAHEAD_HOURS", "72"))
