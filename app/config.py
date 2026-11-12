from __future__ import annotations

import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

_TNumber = TypeVar("_TNumber", int, float)

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_positive_int(name: str, default: int) -> int:
    return int(_env_positive(name, default, int))


def _env_positive_float(name: str, default: float) -> float:
    return float(_env_positive(name, default, float))


def _env_positive(
    name: str, default: _TNumber, parse: Callable[[str], _TNumber]
) -> _TNumber:
    """Parse a positive number from the environment, or return ``default``."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value: _TNumber | None = parse(raw.strip())
    except ValueError:
        value = None
    if isinstance(value, float) and not math.isfinite(value):
        value = None
    if value is None or value <= 0:
        import logging

        logging.getLogger("app.config").warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    return value


def _require_env(name: str) -> str:
    """Return a non-empty env value, or raise (missing station identity must stop startup)."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required (set it in .env)")
    return value


# Tests set TTS_SKIP_DOTENV=1 so a developer's .env cannot change their results.
if not _env_flag("TTS_SKIP_DOTENV"):
    _load_dotenv(ROOT_DIR / ".env")

VOICES_DIR = Path(os.environ.get("TTS_VOICES_DIR", ROOT_DIR / "voices")).expanduser()
PIPER_VOICES_DIR = VOICES_DIR / "piper"
PIPER_VOICES_FILE = Path(
    os.environ.get("TTS_PIPER_VOICES_FILE", ROOT_DIR / "config" / "piper-voices.json")
).expanduser()

DEFAULT_PIPER_VOICE = os.environ.get("TTS_DEFAULT_VOICE", "en_US-ryan-medium")


def _piper_qualities() -> str:
    raw = os.environ.get("TTS_PIPER_QUALITIES", "all").strip().lower()
    if raw in {"all", "medium"}:
        return raw
    import logging

    logging.getLogger("app.config").warning(
        "Invalid TTS_PIPER_QUALITIES=%r; using 'all'",
        os.environ.get("TTS_PIPER_QUALITIES"),
    )
    return "all"


PIPER_QUALITIES = _piper_qualities()

HOST = os.environ.get("TTS_HOST", "0.0.0.0")
PORT = int(os.environ.get("TTS_PORT", "8000"))
MAX_TEXT_CHARS = int(os.environ.get("TTS_MAX_TEXT_CHARS", "5000"))
# Hard ceiling for stored announcement text. The live limit is MAX_TEXT_CHARS.
TEXT_CHARS_CEILING = 20_000
DEFAULT_SPEED = float(os.environ.get("TTS_SPEED", "1.0"))
DEFAULT_SENTENCE_PAUSE = float(os.environ.get("TTS_SENTENCE_PAUSE", "0.25"))
# Bounds for speed / pause / busy retry (Announcement model and /api/speak, /api/announcements).
MIN_SPEECH_SPEED = 0.25
MAX_SPEECH_SPEED = 2.0
MAX_SENTENCE_PAUSE = 2.0
MAX_BUSY_RETRY_SECONDS = 120
MAX_BUSY_GIVE_UP_SECONDS = 600

DATA_DIR = Path(os.environ.get("TTS_DATA_DIR", ROOT_DIR / "data")).expanduser()
NEWSLINE_DIR = DATA_DIR / "arnewsline"
DEFAULT_REPEATER_TIMEOUT_SECONDS = 180
DEFAULT_NEWS_CHECK_START = "12:00"
DEFAULT_NEWS_CHECK_STOP = "18:00"
DEFAULT_NEWS_CHECK_INTERVAL_HOURS = 1.0
MIN_NEWS_CHECK_INTERVAL_HOURS = 0.5
MAX_NEWS_CHECK_INTERVAL_HOURS = 24.0
MIN_REPEATER_TIMEOUT_SECONDS = 30
MAX_REPEATER_TIMEOUT_SECONDS = 600
ANNOUNCEMENTS_PATH = DATA_DIR / "announcements.json"
PHONEMES_PATH = DATA_DIR / "phonemes.json"
USERS_PATH = Path(os.environ.get("TTS_USERS_PATH", DATA_DIR / "users.json")).expanduser()
SESSION_SECRET_PATH = DATA_DIR / ".session_secret"
PCM_CACHE_DIR = Path(os.environ.get("TTS_PCM_CACHE_DIR", DATA_DIR / "pcm")).expanduser()
LOG_DIR = Path(os.environ.get("TTS_LOG_DIR", DATA_DIR / "logs")).expanduser()
LOG_KEEP_DAYS = int(os.environ.get("TTS_LOG_KEEP_DAYS", "30"))
TIMEZONE = os.environ.get("TTS_TIMEZONE", "America/New_York")
PTT_LEAD_SECONDS = float(os.environ.get("TTS_PTT_LEAD_SECONDS", "1"))

def radio_backend() -> str:
    return os.environ.get("TTS_RADIO_BACKEND", "stub").strip().lower()


DRA_USB_CHANNEL = os.environ.get("TTS_DRA_USB_CHANNEL", "left").strip().lower()
if DRA_USB_CHANNEL not in {"left", "right", "mono"}:
    import logging

    logging.getLogger("app.config").warning(
        "Invalid TTS_DRA_USB_CHANNEL=%r; using 'left'",
        os.environ.get("TTS_DRA_USB_CHANNEL"),
    )
    DRA_USB_CHANNEL = "left"  # pyright: ignore[reportConstantRedefinition]
DRA_COS_LINE = os.environ.get("TTS_DRA_COS_LINE", "vol_dn").strip().lower()
if DRA_COS_LINE not in {"vol_dn", "vol_up", "allstar3"}:
    import logging

    logging.getLogger("app.config").warning(
        "Invalid TTS_DRA_COS_LINE=%r; using 'vol_dn'",
        os.environ.get("TTS_DRA_COS_LINE"),
    )
    DRA_COS_LINE = "vol_dn"  # pyright: ignore[reportConstantRedefinition]
DRA_COS_ACTIVE_HIGH = _env_flag("TTS_DRA_COS_ACTIVE_HIGH", False)


def _dra_audio_device_specs() -> list[str] | None:
    raw = os.environ.get("TTS_DRA_AUDIO_DEVICES", "").strip()
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


DRA_AUDIO_DEVICE_SPECS = _dra_audio_device_specs()
DRA_CLEAR_SECONDS = float(os.environ.get("TTS_DRA_CLEAR_SECONDS", "0"))
DRA_CLEAR_POLL_SECONDS = float(os.environ.get("TTS_DRA_CLEAR_POLL_SECONDS", "0.25"))
DRA_FINAL_CHECK_SECONDS = float(os.environ.get("TTS_DRA_FINAL_CHECK_SECONDS", "1.0"))


def dra_cos_track_enabled() -> bool:
    raw = os.environ.get("TTS_DRA_COS_TRACK")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _dra_hid_path_bytes() -> bytes | None:
    raw = os.environ.get("TTS_DRA_HID_PATH", "").strip()
    if not raw:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        import logging

        logging.getLogger("app.config").warning(
            "Invalid TTS_DRA_HID_PATH (expected hex); ignoring"
        )
        return None


DRA_HID_PATH_BYTES = _dra_hid_path_bytes()


def dra_alsa_unmute_enabled() -> bool:
    raw = os.environ.get("TTS_DRA_ALSA_UNMUTE")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DRA_ALSA_SPEAKER_LEVEL = os.environ.get("TTS_DRA_ALSA_SPEAKER_LEVEL", "100%").strip() or "100%"
DRA_ALSA_PLAYBACK_DEVICE = os.environ.get("TTS_DRA_ALSA_PLAYBACK_DEVICE", "").strip()


def dra_prefer_alsa_hw() -> bool:
    """When both exist, choose PortAudio ``(hw:…)`` vs PipeWire ``alsa_output.usb-…``."""
    raw = os.environ.get("TTS_DRA_PREFER_ALSA_HW")
    if raw is None:
        # Default hw: PipeWire sink playback can block forever in PortAudio/sounddevice.
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def dra_linux_playback_backend() -> str:
    """Linux CM119 playback: ``aplay`` to ``plughw`` (default) or ``portaudio``."""
    import sys

    raw = os.environ.get("TTS_DRA_LINUX_PLAYBACK", "aplay").strip().lower()
    if not sys.platform.startswith("linux"):
        return "portaudio"
    if raw in {"aplay", "portaudio"}:
        return raw
    import logging

    logging.getLogger("app.config").warning(
        "Invalid TTS_DRA_LINUX_PLAYBACK=%r; using aplay", raw
    )
    return "aplay"


def _hhmm_env(name: str, default: str) -> str:
    """Return an HH:MM environment value, or ``default`` when it is missing or invalid."""
    from app.model_common import format_hhmm, parse_hhmm

    raw = os.environ.get(name, default).strip() or default
    try:
        return format_hhmm(*parse_hhmm(raw))
    except ValueError:
        import logging

        logging.getLogger("app.config").warning("Invalid %s=%r; using %s", name, raw, default)
        return default


def _news_interval_hours() -> float:
    raw = os.environ.get("TTS_NEWS_CHECK_INTERVAL_HOURS", "").strip()
    if not raw:
        return DEFAULT_NEWS_CHECK_INTERVAL_HOURS
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if (
        value != value
        or value < MIN_NEWS_CHECK_INTERVAL_HOURS
        or value > MAX_NEWS_CHECK_INTERVAL_HOURS
    ):
        import logging

        logging.getLogger("app.config").warning(
            "Invalid TTS_NEWS_CHECK_INTERVAL_HOURS=%r; using %s",
            raw,
            DEFAULT_NEWS_CHECK_INTERVAL_HOURS,
        )
        return DEFAULT_NEWS_CHECK_INTERVAL_HOURS
    return value


def news_check_interval_minutes() -> int:
    """Whole minutes between Newsline checks. 0.5 hours is 30 minutes."""
    return max(1, round(float(NEWS_CHECK_INTERVAL_HOURS) * 60))


def _repeater_timeout_seconds() -> int:
    raw = os.environ.get("TTS_REPEATER_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_REPEATER_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < MIN_REPEATER_TIMEOUT_SECONDS or value > MAX_REPEATER_TIMEOUT_SECONDS:
        import logging

        logging.getLogger("app.config").warning(
            "Invalid TTS_REPEATER_TIMEOUT_SECONDS=%r; using %s",
            raw,
            DEFAULT_REPEATER_TIMEOUT_SECONDS,
        )
        return DEFAULT_REPEATER_TIMEOUT_SECONDS
    return value


DEFAULT_BUSY_RETRY_SECONDS = float(os.environ.get("TTS_BUSY_RETRY_SECONDS", "5"))
DEFAULT_BUSY_GIVE_UP_SECONDS = float(os.environ.get("TTS_BUSY_GIVE_UP_SECONDS", "45"))
SCHEDULER_MAX_WAIT_SECONDS = float(os.environ.get("TTS_SCHEDULER_MAX_WAIT_SECONDS", "5"))
NEWS_CHECK_START = _hhmm_env("TTS_NEWS_CHECK_START", DEFAULT_NEWS_CHECK_START)
NEWS_CHECK_STOP = _hhmm_env("TTS_NEWS_CHECK_STOP", DEFAULT_NEWS_CHECK_STOP)
NEWS_CHECK_INTERVAL_HOURS = _news_interval_hours()
REPEATER_TIMEOUT_SECONDS = _repeater_timeout_seconds()
DEFAULT_SLOT_HALF_WINDOW_MINUTES = int(os.environ.get("TTS_SLOT_HALF_WINDOW_MINUTES", "10"))
LOOKAHEAD_HOURS = int(os.environ.get("TTS_SLOT_LOOKAHEAD_HOURS", "72"))
TRIGGER_NOW = _env_flag("TTS_TRIGGER_NOW", False)
FIRE_VERBOSE_LOG = _env_flag("TTS_FIRE_VERBOSE_LOG", False)
SESSION_IDLE_SECONDS = int(os.environ.get("TTS_SESSION_IDLE_SECONDS", str(30 * 60)))

# Station identity for UI / FastAPI title / idle assertion (call and name are required).
STATION_CALL = _require_env("STATION_CALL")
STATION_NAME = _require_env("STATION_NAME")
APP_TITLE = os.environ.get("APP_TITLE", "").strip() or f"{STATION_CALL} Announcements"


def login_max_failures() -> int:
    """Failed sign-ins allowed per client+username before HTTP 429."""
    return _env_positive_int("TTS_LOGIN_MAX_FAILURES", 5)


def login_window_seconds() -> float:
    """How long those failures stay counted, in seconds."""
    return _env_positive_float("TTS_LOGIN_WINDOW_SECONDS", 300.0)


LOGIN_MAX_FAILURES = login_max_failures()
LOGIN_WINDOW_SECONDS = login_window_seconds()


def fire_verbose_log_enabled() -> bool:
    return FIRE_VERBOSE_LOG


def enforce_text_limit(text: str) -> str:
    """Reject announcement text longer than the current admin setting."""
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"Text must be at most {MAX_TEXT_CHARS} characters")
    return text


def piper_quality_allowed(voice_id: str) -> bool:
    """Return True if this voice id is allowed by TTS_PIPER_QUALITIES."""
    if PIPER_QUALITIES == "medium":
        return voice_id.endswith("-medium")
    return True


def assign_live_settings(
    *,
    station_call: str,
    station_name: str,
    app_title: str,
    default_voice: str,
    speed: float,
    sentence_pause: float,
    ptt_lead_seconds: float,
    busy_retry_seconds: float,
    busy_give_up_seconds: float,
    max_text_chars: int,
    piper_qualities: str,
    timezone: str,
    slot_lookahead_hours: int,
    scheduler_max_wait_seconds: float,
    session_idle_seconds: int,
    login_max_failures: int,
    login_window_seconds: float,
    trigger_now: bool,
    fire_verbose_log: bool,
    news_check_start: str,
    news_check_stop: str,
    news_check_interval_hours: float,
    repeater_timeout_seconds: int,
) -> None:
    """Publish operator settings for the rest of this process.

    Call sites that read these names on ``app.config`` (not a copied import)
    see the new values on the next use.
    """
    raw_title = app_title.strip()
    # These names are module-level so existing call sites can read them live.
    # Direct assignment is rejected because the names are uppercase.
    published: dict[str, object] = {
        "STATION_CALL": station_call,
        "STATION_NAME": station_name,
        "APP_TITLE": raw_title or f"{station_call} Announcements",
        "DEFAULT_PIPER_VOICE": default_voice,
        "DEFAULT_SPEED": speed,
        "DEFAULT_SENTENCE_PAUSE": sentence_pause,
        "PTT_LEAD_SECONDS": ptt_lead_seconds,
        "DEFAULT_BUSY_RETRY_SECONDS": busy_retry_seconds,
        "DEFAULT_BUSY_GIVE_UP_SECONDS": busy_give_up_seconds,
        "MAX_TEXT_CHARS": max_text_chars,
        "PIPER_QUALITIES": piper_qualities,
        "TIMEZONE": timezone,
        "LOOKAHEAD_HOURS": slot_lookahead_hours,
        "SCHEDULER_MAX_WAIT_SECONDS": scheduler_max_wait_seconds,
        "SESSION_IDLE_SECONDS": session_idle_seconds,
        "LOGIN_MAX_FAILURES": login_max_failures,
        "LOGIN_WINDOW_SECONDS": login_window_seconds,
        "TRIGGER_NOW": trigger_now,
        "FIRE_VERBOSE_LOG": fire_verbose_log,
        "NEWS_CHECK_START": news_check_start,
        "NEWS_CHECK_STOP": news_check_stop,
        "NEWS_CHECK_INTERVAL_HOURS": news_check_interval_hours,
        "REPEATER_TIMEOUT_SECONDS": repeater_timeout_seconds,
    }
    module = globals()
    for name, value in published.items():
        module[name] = value

    os.environ["STATION_CALL"] = station_call
    os.environ["STATION_NAME"] = station_name
    os.environ["APP_TITLE"] = raw_title
    os.environ["TTS_DEFAULT_VOICE"] = default_voice
    os.environ["TTS_SPEED"] = _env_number(speed)
    os.environ["TTS_SENTENCE_PAUSE"] = _env_number(sentence_pause)
    os.environ["TTS_PTT_LEAD_SECONDS"] = _env_number(ptt_lead_seconds)
    os.environ["TTS_BUSY_RETRY_SECONDS"] = _env_number(busy_retry_seconds)
    os.environ["TTS_BUSY_GIVE_UP_SECONDS"] = _env_number(busy_give_up_seconds)
    os.environ["TTS_MAX_TEXT_CHARS"] = str(max_text_chars)
    os.environ["TTS_PIPER_QUALITIES"] = piper_qualities
    os.environ["TTS_TIMEZONE"] = timezone
    os.environ["TTS_SLOT_LOOKAHEAD_HOURS"] = str(slot_lookahead_hours)
    os.environ["TTS_SCHEDULER_MAX_WAIT_SECONDS"] = _env_number(scheduler_max_wait_seconds)
    os.environ["TTS_SESSION_IDLE_SECONDS"] = str(session_idle_seconds)
    os.environ["TTS_LOGIN_MAX_FAILURES"] = str(login_max_failures)
    os.environ["TTS_LOGIN_WINDOW_SECONDS"] = _env_number(login_window_seconds)
    os.environ["TTS_TRIGGER_NOW"] = "true" if trigger_now else "false"
    os.environ["TTS_FIRE_VERBOSE_LOG"] = "true" if fire_verbose_log else "false"
    os.environ["TTS_NEWS_CHECK_START"] = news_check_start
    os.environ["TTS_NEWS_CHECK_STOP"] = news_check_stop
    os.environ["TTS_NEWS_CHECK_INTERVAL_HOURS"] = _env_number(news_check_interval_hours)
    os.environ["TTS_REPEATER_TIMEOUT_SECONDS"] = str(repeater_timeout_seconds)


def _env_number(value: float) -> str:
    return format(value, "g")


def editable_env_updates() -> dict[str, str]:
    """The .env keys the admin form owns, from the values last published."""
    return {
        "STATION_CALL": STATION_CALL,
        "STATION_NAME": STATION_NAME,
        "APP_TITLE": os.environ.get("APP_TITLE", "").strip(),
        "TTS_DEFAULT_VOICE": DEFAULT_PIPER_VOICE,
        "TTS_SPEED": _env_number(DEFAULT_SPEED),
        "TTS_SENTENCE_PAUSE": _env_number(DEFAULT_SENTENCE_PAUSE),
        "TTS_PTT_LEAD_SECONDS": _env_number(PTT_LEAD_SECONDS),
        "TTS_BUSY_RETRY_SECONDS": _env_number(DEFAULT_BUSY_RETRY_SECONDS),
        "TTS_BUSY_GIVE_UP_SECONDS": _env_number(DEFAULT_BUSY_GIVE_UP_SECONDS),
        "TTS_MAX_TEXT_CHARS": str(MAX_TEXT_CHARS),
        "TTS_PIPER_QUALITIES": PIPER_QUALITIES,
        "TTS_TIMEZONE": TIMEZONE,
        "TTS_SLOT_LOOKAHEAD_HOURS": str(LOOKAHEAD_HOURS),
        "TTS_SCHEDULER_MAX_WAIT_SECONDS": _env_number(SCHEDULER_MAX_WAIT_SECONDS),
        "TTS_SESSION_IDLE_SECONDS": str(SESSION_IDLE_SECONDS),
        "TTS_LOGIN_MAX_FAILURES": str(LOGIN_MAX_FAILURES),
        "TTS_LOGIN_WINDOW_SECONDS": _env_number(LOGIN_WINDOW_SECONDS),
        "TTS_TRIGGER_NOW": "true" if TRIGGER_NOW else "false",
        "TTS_FIRE_VERBOSE_LOG": "true" if FIRE_VERBOSE_LOG else "false",
        "TTS_NEWS_CHECK_START": NEWS_CHECK_START,
        "TTS_NEWS_CHECK_STOP": NEWS_CHECK_STOP,
        "TTS_NEWS_CHECK_INTERVAL_HOURS": _env_number(NEWS_CHECK_INTERVAL_HOURS),
        "TTS_REPEATER_TIMEOUT_SECONDS": str(REPEATER_TIMEOUT_SECONDS),
    }


def session_secret() -> str:
    """Return the session signing secret, creating a persisted one if needed."""
    configured = os.environ.get("TTS_SESSION_SECRET", "").strip()
    if configured:
        return configured
    if SESSION_SECRET_PATH.is_file():
        value = SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value
    import secrets

    value = secrets.token_urlsafe(48)
    SESSION_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ = SESSION_SECRET_PATH.write_text(value + "\n", encoding="utf-8")
    try:
        SESSION_SECRET_PATH.chmod(0o600)
    except OSError:
        pass
    return value
