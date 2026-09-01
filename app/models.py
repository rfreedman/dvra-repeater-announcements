from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import (
    DEFAULT_BUSY_GIVE_UP_SECONDS,
    DEFAULT_BUSY_RETRY_SECONDS,
    DEFAULT_SENTENCE_PAUSE,
    DEFAULT_SPEED,
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


def _normalize_times(values: list[str]) -> list[str]:
    if not values:
        raise ValueError("At least one time is required")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        stamp = format_hhmm(*parse_hhmm(raw))
        if stamp not in seen:
            seen.add(stamp)
            normalized.append(stamp)
    normalized.sort(key=lambda stamp: parse_hhmm(stamp))
    return normalized


def _normalize_monthdays(values: list[int]) -> list[int]:
    out: list[int] = []
    for item in values:
        if item < 1 or item > 31:
            raise ValueError("day of month must be 1–31")
        if item not in out:
            out.append(item)
    if not out:
        raise ValueError("monthly schedule needs at least one day of the month")
    out.sort()
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


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Exclusion(BaseModel):
    kind: Literal["day", "day_time"] = "day"
    days: list[str]
    start: str | None = None
    end: str | None = None
    time: str | None = None
    note: str | None = None

    @field_validator("days")
    @classmethod
    def days_known(cls, value: list[str]) -> list[str]:
        return normalize_days(value)

    @field_validator("note")
    @classmethod
    def note_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if len(text) > 120:
            raise ValueError("Note must be 120 characters or fewer")
        return text

    @model_validator(mode="after")
    def normalize_range(self) -> Exclusion:
        start = self.start
        end = self.end
        if self.time and not start and not end:
            start = end = self.time
        if bool(start) != bool(end):
            raise ValueError("Exclusion range needs both a start and an end time")
        if start and end:
            self.start = format_hhmm(*parse_hhmm(start))
            self.end = format_hhmm(*parse_hhmm(end))
            self.kind = "day_time"
        else:
            self.start = None
            self.end = None
            self.kind = "day"
        self.time = None
        return self


class Schedule(BaseModel):
    id: str = Field(default_factory=new_id)
    enabled: bool = True
    kind: Literal["hourly", "daily", "weekly", "monthly", "once"]
    timezone: str = TIMEZONE
    minute: int | None = None
    minutes: list[int] = Field(default_factory=list)
    times: list[str] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    monthdays: list[int] = Field(default_factory=list)
    occurrence: int | None = None
    skip_months: list[int] = Field(default_factory=list)
    at: datetime | None = None
    exclusions: list[Exclusion] = Field(default_factory=list)
    last_run_at: datetime | None = None

    @model_validator(mode="after")
    def kind_fields(self) -> Schedule:
        ZoneInfo(self.timezone)
        if self.kind == "hourly":
            values = list(self.minutes)
            if self.minute is not None:
                values.append(self.minute)
            normalized: list[int] = []
            for item in values:
                if item < 0 or item > 59:
                    raise ValueError("minute must be 0–59")
                if item not in normalized:
                    normalized.append(item)
            if not normalized:
                raise ValueError("hourly schedule needs at least one minute")
            self.minutes = normalized
            self.minute = None
            self.times = []
            self.days = []
            self.monthdays = []
            self.occurrence = None
            self.skip_months = []
            self.at = None
        elif self.kind == "daily":
            self.times = _normalize_times(self.times)
            self.minute = None
            self.minutes = []
            self.days = []
            self.monthdays = []
            self.occurrence = None
            self.skip_months = []
            self.at = None
        elif self.kind == "weekly":
            self.times = _normalize_times(self.times)
            self.days = normalize_days(self.days)
            self.minute = None
            self.minutes = []
            self.monthdays = []
            self.occurrence = None
            self.skip_months = []
            self.at = None
        elif self.kind == "monthly":
            self.times = _normalize_times(self.times)
            self.skip_months = _normalize_skip_months(self.skip_months)
            self.minute = None
            self.minutes = []
            self.at = None
            by_date = bool(self.monthdays)
            by_weekday = bool(self.days) or self.occurrence is not None
            if by_date and by_weekday:
                raise ValueError("monthly schedule is either day-of-month or weekday, not both")
            if by_date:
                self.monthdays = _normalize_monthdays(self.monthdays)
                self.days = []
                self.occurrence = None
            elif self.days and self.occurrence is not None:
                if self.occurrence not in {1, 2, 3, 4, -1}:
                    raise ValueError("occurrence must be 1–4 or last")
                self.days = normalize_days(self.days)
                if len(self.days) != 1:
                    raise ValueError("monthly weekday schedule needs exactly one weekday")
                self.monthdays = []
            else:
                raise ValueError("monthly schedule needs month days or a weekday occurrence")
        else:
            if self.at is None:
                raise ValueError("once schedule needs a date and time")
            zone = ZoneInfo(self.timezone)
            if self.at.tzinfo is None:
                self.at = self.at.replace(tzinfo=zone)
            else:
                self.at = self.at.astimezone(zone)
            self.minute = None
            self.minutes = []
            self.times = []
            self.days = []
            self.monthdays = []
            self.occurrence = None
            self.skip_months = []
            self.exclusions = []
        if self.last_run_at is not None and self.last_run_at.tzinfo is None:
            self.last_run_at = self.last_run_at.replace(tzinfo=ZoneInfo(self.timezone))
        return self


class Announcement(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    text: str
    voice: str | None = None
    speed: float = DEFAULT_SPEED
    sentence_pause: float = DEFAULT_SENTENCE_PAUSE
    busy_retry_seconds: float = DEFAULT_BUSY_RETRY_SECONDS
    busy_give_up_seconds: float = DEFAULT_BUSY_GIVE_UP_SECONDS
    schedules: list[Schedule] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_present(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Name is required")
        return name

    def schedule_by_id(self, schedule_id: str) -> Schedule | None:
        for item in self.schedules:
            if item.id == schedule_id:
                return item
        return None
