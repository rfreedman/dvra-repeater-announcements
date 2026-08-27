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
