from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import (
    DEFAULT_BUSY_GIVE_UP_SECONDS,
    DEFAULT_BUSY_RETRY_SECONDS,
    DEFAULT_NEWS_CHECK_START,
    DEFAULT_NEWS_CHECK_STOP,
    DEFAULT_REPEATER_TIMEOUT_SECONDS,
    DEFAULT_SENTENCE_PAUSE,
    DEFAULT_SLOT_HALF_WINDOW_MINUTES,
    DEFAULT_SPEED,
    MAX_BUSY_GIVE_UP_SECONDS,
    MAX_BUSY_RETRY_SECONDS,
    MAX_REPEATER_TIMEOUT_SECONDS,
    MAX_SENTENCE_PAUSE,
    MAX_SPEECH_SPEED,
    MAX_TEXT_CHARS,
    MIN_REPEATER_TIMEOUT_SECONDS,
    MIN_SPEECH_SPEED,
    TIMEZONE,
)

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}
MONTH_LABELS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

ScheduleKind = Literal["baseline", "weekly", "monthly", "range", "monthly_range", "emergency"]
AnnouncementKind = Literal["tts", "newsline"]
OVERLAY_KINDS = frozenset({"weekly", "monthly", "range", "monthly_range", "emergency"})
DEFAULT_PRIORITY = {
    "baseline": 0,
    "weekly": 50,
    "monthly": 50,
    "range": 20,
    "monthly_range": 20,
    "emergency": 100,
}


def schedule_meta() -> dict[str, object]:
    """Day and month labels plus template priorities for the schedule editor."""
    return {
        "days": [{"id": day, "label": DAY_LABELS[day]} for day in DAY_NAMES],
        "months": [{"id": month, "label": MONTH_LABELS[month]} for month in range(1, 13)],
        "default_priority": dict(DEFAULT_PRIORITY),
    }


def normalize_days(value: list[str]) -> list[str]:
    out: list[str] = []
    for day in value:
        key = day.strip().lower()[:3]
        if key not in DAY_NAMES:
            raise ValueError(f"Unknown day {day!r}")
        if key not in out:
            out.append(key)
    if not out:
        raise ValueError("At least one day is required")
    out.sort(key=DAY_NAMES.index)
    return out


def _normalize_skip_months(values: list[int]) -> list[int]:
    out: list[int] = []
    for item in values:
        if item < 1 or item > 12:
            raise ValueError("month must be 1–12")
        if item not in out:
            out.append(item)
    out.sort()
    return out


def parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Time must be HH:MM, got {value!r}")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Time out of range: {value!r}")
    return hour, minute


def format_hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


SLOT_MINUTES = (0, 30)


def is_slot_stamp(value: str) -> bool:
    _hour, minute = parse_hhmm(value)
    return minute in SLOT_MINUTES


def normalize_slots(values: list[str], *, required: bool) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not is_slot_stamp(raw):
            raise ValueError(f"Slot must be on the hour or half-hour, got {raw!r}")
        stamp = format_hhmm(*parse_hhmm(raw))
        if stamp not in seen:
            seen.add(stamp)
            normalized.append(stamp)
    normalized.sort(key=lambda stamp: parse_hhmm(stamp))
    if required and not normalized:
        raise ValueError("At least one hour or half-hour slot is required")
    return normalized


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def normalize_announcement_text(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("Text is required")
    return text


def normalize_entity_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("Name is required")
    return name


class Announcement(BaseModel):
    """Library item. Spoken text, or a Newsline bulletin that plays story audio."""

    id: str = Field(default_factory=new_id)
    name: str
    kind: AnnouncementKind = "tts"
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    voice: str | None = None
    speed: float = Field(default=DEFAULT_SPEED, gt=MIN_SPEECH_SPEED, le=MAX_SPEECH_SPEED)
    sentence_pause: float = Field(
        default=DEFAULT_SENTENCE_PAUSE, ge=0.0, le=MAX_SENTENCE_PAUSE
    )
    busy_retry_seconds: float = Field(
        default=DEFAULT_BUSY_RETRY_SECONDS, gt=0, le=MAX_BUSY_RETRY_SECONDS
    )
    busy_give_up_seconds: float = Field(
        default=DEFAULT_BUSY_GIVE_UP_SECONDS, gt=0, le=MAX_BUSY_GIVE_UP_SECONDS
    )
    play_seconds: float | None = None
    too_long: bool = False
    opening_announcement_id: str | None = None
    station_id_announcement_id: str | None = None
    closing_announcement_id: str | None = None
    ptt_gap_seconds: float = Field(default=2.0, ge=0.0, le=30.0)

    @field_validator("name")
    @classmethod
    def name_present(cls, value: str) -> str:
        return normalize_entity_name(value)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def kind_fields(self) -> Announcement:
        if self.kind == "newsline":
            self.text = ""
            if not self.station_id_announcement_id:
                raise ValueError("A station ID announcement is required")
            return self
        self.text = normalize_announcement_text(self.text)
        return self


class Schedule(BaseModel):
    """When a library announcement (or silence) occupies a half-hour slot."""

    id: str = Field(default_factory=new_id)
    name: str
    enabled: bool = True
    kind: ScheduleKind
    # Stored for compatibility only. Slot math always uses the document's
    # Settings.timezone so every schedule shares one station clock.
    timezone: str = TIMEZONE
    announcement_id: str | None = None
    offset_minutes: int = 0
    slots: list[str] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    occurrence: int | None = None
    skip_months: list[int] = Field(default_factory=list)
    start_date: date | None = None
    event_date: date | None = None
    event_slot: str | None = None
    days_before: int | None = None
    priority: int = 0
    last_run_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def name_present(cls, value: str) -> str:
        return normalize_entity_name(value)

    @model_validator(mode="after")
    def kind_fields(self) -> Schedule:
        ZoneInfo(self.timezone)
        if self.kind == "emergency":
            self.priority = DEFAULT_PRIORITY["emergency"]
        elif self.priority == 0:
            self.priority = DEFAULT_PRIORITY[self.kind]
        if self.kind == "baseline":
            if not self.announcement_id:
                raise ValueError("A baseline schedule needs an announcement")
            self.offset_minutes = 0
            self.slots = []
            self.days = []
            self.occurrence = None
            self.skip_months = []
            self.start_date = None
            self.event_date = None
            self.event_slot = None
            self.days_before = None
            self.priority = DEFAULT_PRIORITY["baseline"]
        elif self.kind == "weekly":
            self.days = normalize_days(self.days)
            self.slots = normalize_slots(self.slots, required=True)
            self._clear_monthly_and_dates()
        elif self.kind == "monthly":
            self._require_monthly_weekday()
            self.slots = normalize_slots(self.slots, required=True)
            self.start_date = None
            self.event_date = None
            self.event_slot = None
            self.days_before = None
        elif self.kind == "range":
            if self.start_date is None or self.event_date is None or not self.event_slot:
                raise ValueError("An event countdown needs a start date and an event slot")
            if not is_slot_stamp(self.event_slot):
                raise ValueError("Event slot must be on the hour or half-hour")
            self.event_slot = format_hhmm(*parse_hhmm(self.event_slot))
            if self.event_date < self.start_date:
                raise ValueError("Event date cannot be before the start date")
            self.slots = normalize_slots(self.slots, required=False)
            self.days = []
            self.occurrence = None
            self.skip_months = []
            self.days_before = None
        elif self.kind == "monthly_range":
            self._require_monthly_weekday()
            if self.days_before is None:
                raise ValueError("A monthly countdown needs how many days before the event to start")
            if self.days_before < 0 or self.days_before > 90:
                raise ValueError("Days before the event must be 0–90")
            if not self.event_slot:
                raise ValueError("A monthly countdown needs an event slot")
            if not is_slot_stamp(self.event_slot):
                raise ValueError("Event slot must be on the hour or half-hour")
            self.event_slot = format_hhmm(*parse_hhmm(self.event_slot))
            self.slots = normalize_slots(self.slots, required=False)
            self.start_date = None
            self.event_date = None
        else:
            self.slots = []
            self.days = []
            self.occurrence = None
            self.skip_months = []
            self.start_date = None
            self.event_date = None
            self.event_slot = None
            self.days_before = None
        if self.last_run_at is not None and self.last_run_at.tzinfo is None:
            self.last_run_at = self.last_run_at.replace(tzinfo=ZoneInfo(self.timezone))
        return self

    def _clear_monthly_and_dates(self) -> None:
        self.occurrence = None
        self.skip_months = []
        self.start_date = None
        self.event_date = None
        self.event_slot = None
        self.days_before = None

    def _require_monthly_weekday(self) -> None:
        self.days = normalize_days(self.days)
        if len(self.days) != 1:
            raise ValueError("A monthly schedule needs exactly one weekday")
        if self.occurrence not in {1, 2, 3, 4}:
            raise ValueError("Monthly occurrence must be 1st, 2nd, 3rd, or 4th")
        self.skip_months = _normalize_skip_months(self.skip_months)

    @property
    def is_silence(self) -> bool:
        return self.kind != "baseline" and not self.announcement_id

    @property
    def is_overlay(self) -> bool:
        return self.kind in OVERLAY_KINDS


class Settings(BaseModel):
    timezone: str = TIMEZONE
    slot_half_window_minutes: int = DEFAULT_SLOT_HALF_WINDOW_MINUTES
    baseline_randomize: bool = False
    news_check_start: str = DEFAULT_NEWS_CHECK_START
    news_check_stop: str = DEFAULT_NEWS_CHECK_STOP
    repeater_timeout_seconds: int = DEFAULT_REPEATER_TIMEOUT_SECONDS

    @field_validator("slot_half_window_minutes")
    @classmethod
    def window_range(cls, value: int) -> int:
        if value < 0 or value > 29:
            raise ValueError("Slot window must be 0–29 minutes")
        return value

    @field_validator("news_check_start", "news_check_stop")
    @classmethod
    def clock_time(cls, value: str) -> str:
        return format_hhmm(*parse_hhmm(value))

    @field_validator("repeater_timeout_seconds")
    @classmethod
    def timeout_range(cls, value: int) -> int:
        if value < MIN_REPEATER_TIMEOUT_SECONDS or value > MAX_REPEATER_TIMEOUT_SECONDS:
            raise ValueError(
                f"Repeater timeout must be {MIN_REPEATER_TIMEOUT_SECONDS}–"
                f"{MAX_REPEATER_TIMEOUT_SECONDS} seconds"
            )
        return value


class Rotation(BaseModel):
    """Persisted walk through the baseline pool. `index` is the next schedule to use."""

    order: list[str] = Field(default_factory=list)
    index: int = 0
    last_consumed_slot: str | None = None


class StoreDocument(BaseModel):
    version: Literal[2] = 2
    settings: Settings = Field(default_factory=Settings)
    announcements: list[Announcement] = Field(default_factory=list)
    schedules: list[Schedule] = Field(default_factory=list)
    rotation: Rotation = Field(default_factory=Rotation)

    def announcement_by_id(self, announcement_id: str | None) -> Announcement | None:
        if not announcement_id:
            return None
        for item in self.announcements:
            if item.id == announcement_id:
                return item
        return None

    def schedule_by_id(self, schedule_id: str | None) -> Schedule | None:
        if not schedule_id:
            return None
        for item in self.schedules:
            if item.id == schedule_id:
                return item
        return None

    def enabled_baselines(self) -> list[Schedule]:
        return [item for item in self.schedules if item.kind == "baseline" and item.enabled]
