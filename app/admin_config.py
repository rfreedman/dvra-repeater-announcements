"""Operator-editable settings, saved history, and the reload generation counter.

History lives in versioned JSON. A save appends an entry. A revert points
``current_id`` at an older entry and applies that entry's values again.
"""

from __future__ import annotations

import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, model_validator

from app import config
from app.config import (
    DATA_DIR,
    MAX_BUSY_GIVE_UP_SECONDS,
    MAX_BUSY_RETRY_SECONDS,
    MAX_SENTENCE_PAUSE,
    MAX_SPEECH_SPEED,
    MIN_SPEECH_SPEED,
    ROOT_DIR,
    TEXT_CHARS_CEILING,
    assign_live_settings,
    editable_env_updates,
)
from app.dotenv_file import update_dotenv
from app.json_io import read_versioned_json, write_json_atomic
from app.model_common import format_hhmm, new_id, parse_hhmm

SHORT_DESCRIPTION_MAX = 120
LONG_DESCRIPTION_MAX = 4_000
IMPORTED_DESCRIPTION = "Imported from .env"

_lock = threading.RLock()
_generation = 0


class RuntimeConfigValues(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    station_call: str
    station_name: str
    app_title: str = ""
    default_voice: str
    speed: float
    sentence_pause: float
    ptt_lead_seconds: float
    busy_retry_seconds: float
    busy_give_up_seconds: float
    max_text_chars: int
    piper_qualities: Literal["all", "medium"]
    timezone: str
    slot_lookahead_hours: int
    scheduler_max_wait_seconds: float
    session_idle_seconds: int
    login_max_failures: int
    login_window_seconds: float
    trigger_now: bool
    fire_verbose_log: bool
    news_check_start: str = "12:00"
    news_check_stop: str = "18:00"
    news_check_interval_hours: float = 1.0
    repeater_timeout_seconds: int = 180

    @model_validator(mode="after")
    def bounds(self) -> RuntimeConfigValues:
        self.station_call = _required_text(self.station_call, "Station call", 32)
        self.station_name = _required_text(self.station_name, "Station name", 80)
        self.app_title = _optional_text(self.app_title, "App title", 80)
        self.timezone = _timezone_name(self.timezone)
        self.default_voice = _voice_id(self.default_voice, self.piper_qualities)
        self.speed = _float_range(
            self.speed, "Speed", low=MIN_SPEECH_SPEED, high=MAX_SPEECH_SPEED, low_inclusive=False
        )
        self.sentence_pause = _float_range(
            self.sentence_pause, "Sentence pause", low=0, high=MAX_SENTENCE_PAUSE
        )
        self.ptt_lead_seconds = _float_range(
            self.ptt_lead_seconds, "PTT lead", low=0, high=10
        )
        self.busy_retry_seconds = _float_range(
            self.busy_retry_seconds,
            "Busy retry",
            low=0,
            high=MAX_BUSY_RETRY_SECONDS,
            low_inclusive=False,
        )
        self.busy_give_up_seconds = _float_range(
            self.busy_give_up_seconds,
            "Busy give-up",
            low=0,
            high=MAX_BUSY_GIVE_UP_SECONDS,
            low_inclusive=False,
        )
        self.max_text_chars = _int_range(self.max_text_chars, "Max text length", 1, TEXT_CHARS_CEILING)
        self.slot_lookahead_hours = _int_range(
            self.slot_lookahead_hours, "Lookahead", 1, 24 * 14
        )
        self.scheduler_max_wait_seconds = _float_range(
            self.scheduler_max_wait_seconds, "Scheduler wake", low=0.5, high=60
        )
        self.session_idle_seconds = _int_range(
            self.session_idle_seconds, "Session idle", 60, 24 * 60 * 60
        )
        self.login_max_failures = _int_range(self.login_max_failures, "Login failures", 1, 50)
        self.login_window_seconds = _float_range(
            self.login_window_seconds, "Login window", low=10, high=3600
        )
        self.news_check_start = format_hhmm(*parse_hhmm(self.news_check_start))
        self.news_check_stop = format_hhmm(*parse_hhmm(self.news_check_stop))
        self.news_check_interval_hours = _float_range(
            self.news_check_interval_hours,
            "Newsline check interval",
            low=config.MIN_NEWS_CHECK_INTERVAL_HOURS,
            high=config.MAX_NEWS_CHECK_INTERVAL_HOURS,
        )
        self.repeater_timeout_seconds = _int_range(
            self.repeater_timeout_seconds,
            "Repeater timeout",
            config.MIN_REPEATER_TIMEOUT_SECONDS,
            config.MAX_REPEATER_TIMEOUT_SECONDS,
        )
        return self


class ConfigHistoryEntry(BaseModel):
    id: str
    saved_at: str
    short_description: str
    long_description: str = ""
    values: RuntimeConfigValues


class ConfigHistoryFile(BaseModel):
    version: Literal[1] = 1
    current_id: str
    entries: list[ConfigHistoryEntry]


class ConfigEntrySummary(BaseModel):
    id: str
    saved_at: str
    short_description: str
    long_description: str
    current: bool


class ConfigCurrent(ConfigEntrySummary):
    values: RuntimeConfigValues


class ConfigState(BaseModel):
    current: ConfigCurrent
    history: list[ConfigEntrySummary]
    generation: int
    changed: bool = True


def config_generation() -> int:
    with _lock:
        return _generation


def bump_config_generation() -> int:
    with _lock:
        _bump()
        return _generation


def set_config_generation(value: int) -> None:
    """Test helper. Production code uses ``bump_config_generation``."""
    global _generation
    with _lock:
        _generation = value


def dotenv_path() -> Path:
    override = os.environ.get("TTS_DOTENV_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    if os.environ.get("TTS_SKIP_DOTENV", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("Refusing to write .env while TTS_SKIP_DOTENV is set; set TTS_DOTENV_PATH")
    return ROOT_DIR / ".env"


def history_path() -> Path:
    override = os.environ.get("TTS_CONFIG_HISTORY_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return DATA_DIR / "config_history.json"


def current_values() -> RuntimeConfigValues:
    qualities: Literal["all", "medium"] = "medium" if config.PIPER_QUALITIES == "medium" else "all"
    return RuntimeConfigValues(
        station_call=config.STATION_CALL,
        station_name=config.STATION_NAME,
        app_title=os.environ.get("APP_TITLE", "").strip(),
        default_voice=config.DEFAULT_PIPER_VOICE,
        speed=config.DEFAULT_SPEED,
        sentence_pause=config.DEFAULT_SENTENCE_PAUSE,
        ptt_lead_seconds=config.PTT_LEAD_SECONDS,
        busy_retry_seconds=config.DEFAULT_BUSY_RETRY_SECONDS,
        busy_give_up_seconds=config.DEFAULT_BUSY_GIVE_UP_SECONDS,
        max_text_chars=config.MAX_TEXT_CHARS,
        piper_qualities=qualities,
        timezone=config.TIMEZONE,
        slot_lookahead_hours=config.LOOKAHEAD_HOURS,
        scheduler_max_wait_seconds=config.SCHEDULER_MAX_WAIT_SECONDS,
        session_idle_seconds=config.SESSION_IDLE_SECONDS,
        login_max_failures=config.login_max_failures(),
        login_window_seconds=config.login_window_seconds(),
        trigger_now=config.TRIGGER_NOW,
        fire_verbose_log=config.FIRE_VERBOSE_LOG,
        news_check_start=config.NEWS_CHECK_START,
        news_check_stop=config.NEWS_CHECK_STOP,
        news_check_interval_hours=config.NEWS_CHECK_INTERVAL_HOURS,
        repeater_timeout_seconds=config.REPEATER_TIMEOUT_SECONDS,
    )


def ensure_history() -> ConfigHistoryFile:
    with _lock:
        document = _read_history()
        if document is not None:
            return document
        return _seed_unlocked()


def save_config(
    values: RuntimeConfigValues,
    short_description: str,
    long_description: str,
) -> ConfigHistoryFile:
    short = clean_short(short_description)
    long = clean_long(long_description)
    checked = RuntimeConfigValues.model_validate(values.model_dump())
    with _lock:
        _apply(checked)
        document = _read_history()
        entries = list(document.entries) if document is not None else []
        entry = _make_entry(checked, short, long)
        entries.append(entry)
        updated = ConfigHistoryFile(version=1, current_id=entry.id, entries=entries)
        _write_history(updated)
        _bump()
        return updated


def revert_config(entry_id: str) -> tuple[ConfigHistoryFile, bool]:
    with _lock:
        document = _read_history()
        if document is None:
            document = _seed_unlocked()
        if document.current_id == entry_id:
            return document, False
        match = next((item for item in document.entries if item.id == entry_id), None)
        if match is None:
            raise KeyError(entry_id)
        checked = RuntimeConfigValues.model_validate(match.values.model_dump())
        _apply(checked)
        updated = document.model_copy(update={"current_id": match.id})
        _write_history(updated)
        _bump()
        return updated, True


def config_state(document: ConfigHistoryFile, *, changed: bool) -> ConfigState:
    current = _current_entry(document)
    return ConfigState(
        current=ConfigCurrent(
            id=current.id,
            saved_at=current.saved_at,
            short_description=current.short_description,
            long_description=current.long_description,
            current=True,
            values=current.values,
        ),
        history=[
            ConfigEntrySummary(
                id=item.id,
                saved_at=item.saved_at,
                short_description=item.short_description,
                long_description=item.long_description,
                current=item.id == document.current_id,
            )
            for item in document.entries
        ],
        generation=config_generation(),
        changed=changed,
    )


def clean_short(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("A short description is required")
    if len(text) > SHORT_DESCRIPTION_MAX:
        raise ValueError(f"Short description must be at most {SHORT_DESCRIPTION_MAX} characters")
    return text


def clean_long(value: str) -> str:
    text = value.strip()
    if len(text) > LONG_DESCRIPTION_MAX:
        raise ValueError(f"Long description must be at most {LONG_DESCRIPTION_MAX} characters")
    return text


def _apply(values: RuntimeConfigValues) -> None:
    assign_live_settings(
        station_call=values.station_call,
        station_name=values.station_name,
        app_title=values.app_title,
        default_voice=values.default_voice,
        speed=values.speed,
        sentence_pause=values.sentence_pause,
        ptt_lead_seconds=values.ptt_lead_seconds,
        busy_retry_seconds=values.busy_retry_seconds,
        busy_give_up_seconds=values.busy_give_up_seconds,
        max_text_chars=values.max_text_chars,
        piper_qualities=values.piper_qualities,
        timezone=values.timezone,
        slot_lookahead_hours=values.slot_lookahead_hours,
        scheduler_max_wait_seconds=values.scheduler_max_wait_seconds,
        session_idle_seconds=values.session_idle_seconds,
        login_max_failures=values.login_max_failures,
        login_window_seconds=values.login_window_seconds,
        trigger_now=values.trigger_now,
        fire_verbose_log=values.fire_verbose_log,
        news_check_start=values.news_check_start,
        news_check_stop=values.news_check_stop,
        news_check_interval_hours=values.news_check_interval_hours,
        repeater_timeout_seconds=values.repeater_timeout_seconds,
    )
    update_dotenv(dotenv_path(), editable_env_updates())
    _sync_runtime(values)


def _sync_runtime(values: RuntimeConfigValues) -> None:
    from app.auth import login_limiter
    from app.scheduler import PromptBackgroundScheduler
    from app.scheduler_instance import get_scheduler
    from app.scheduler_jobs import sync_jobs
    from app.store import get_store

    login_limiter.max_failures = values.login_max_failures
    login_limiter.window_seconds = values.login_window_seconds
    scheduler = get_scheduler()
    if isinstance(scheduler, PromptBackgroundScheduler):
        scheduler.max_wait_seconds = max(0.5, values.scheduler_max_wait_seconds)
    store = get_store()
    settings = store.document().settings
    if settings.timezone != values.timezone:
        _ = store.save_settings(settings.model_copy(update={"timezone": values.timezone}))
    sync_jobs()
    from app.newsline.poll import arm_next_poll
    from app.spoken_length import recompute_too_long

    arm_next_poll(immediate=False)
    timeout = int(config.REPEATER_TIMEOUT_SECONDS)
    length_changed = False
    for item in list(store.document().announcements):
        if item.kind != "tts":
            continue
        updated = recompute_too_long(item, timeout)
        if updated.kind == "tts" and updated.too_long != item.too_long:
            _ = store.save_announcement(updated)
            length_changed = True
    if length_changed:
        from app.live_notify import notify_home_data_changed

        notify_home_data_changed()


def _seed_unlocked() -> ConfigHistoryFile:
    entry = _make_entry(current_values(), IMPORTED_DESCRIPTION, "")
    document = ConfigHistoryFile(version=1, current_id=entry.id, entries=[entry])
    _write_history(document)
    return document


def _make_entry(values: RuntimeConfigValues, short: str, long: str) -> ConfigHistoryEntry:
    return ConfigHistoryEntry(
        id=new_id(),
        saved_at=datetime.now(timezone.utc).isoformat(),
        short_description=short,
        long_description=long,
        values=values,
    )


def _read_history() -> ConfigHistoryFile | None:
    raw = read_versioned_json(history_path(), version=1, kind="config history")
    if raw is None:
        return None
    return ConfigHistoryFile.model_validate(raw)


def _write_history(document: ConfigHistoryFile) -> None:
    write_json_atomic(history_path(), document.model_dump(mode="json"))


def _current_entry(document: ConfigHistoryFile) -> ConfigHistoryEntry:
    for item in document.entries:
        if item.id == document.current_id:
            return item
    raise RuntimeError("Config history has no current entry")


def _bump() -> None:
    global _generation
    _generation += 1


def _required_text(value: str, label: str, limit: int) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > limit:
        raise ValueError(f"{label} must be at most {limit} characters")
    return text


def _optional_text(value: str, label: str, limit: int) -> str:
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{label} must be at most {limit} characters")
    return text


def _timezone_name(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("Timezone is required")
    try:
        _ = ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone {text}") from exc
    return text


def _voice_id(voice_id: str, qualities: str) -> str:
    text = voice_id.strip()
    if not text:
        raise ValueError("Default voice is required")
    if qualities == "medium" and not text.endswith("-medium"):
        raise ValueError("Default voice must be a medium voice when qualities are limited to medium")
    from piper.download_voices import VOICE_PATTERN

    from app.tts.piper import featured_voices

    known = any(meta.id == text for meta in featured_voices)
    if not known and VOICE_PATTERN.match(text) is None:
        raise ValueError(f"Unknown Piper voice {text}")
    return text


def _float_range(
    value: float,
    label: str,
    *,
    low: float,
    high: float,
    low_inclusive: bool = True,
) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if low_inclusive:
        ok = low <= value <= high
        bound = f"{low:g}–{high:g}"
    else:
        ok = low < value <= high
        bound = f"greater than {low:g} and at most {high:g}"
    if not ok:
        raise ValueError(f"{label} must be {bound}")
    return value


def _int_range(value: int, label: str, low: int, high: int) -> int:
    if value < low or value > high:
        raise ValueError(f"{label} must be {low}–{high}")
    return value
