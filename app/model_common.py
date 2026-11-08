"""Constants and field normalizers shared by the persisted models."""

from __future__ import annotations

import uuid
from typing import Literal

DayName = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES: tuple[DayName, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_BY_KEY: dict[str, DayName] = {day: day for day in DAY_NAMES}
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


def normalize_days(value: list[str]) -> list[DayName]:
    out: list[DayName] = []
    for day in value:
        key = _DAY_BY_KEY.get(day.strip().lower()[:3])
        if key is None:
            raise ValueError(f"Unknown day {day!r}")
        if key not in out:
            out.append(key)
    if not out:
        raise ValueError("At least one day is required")
    out.sort(key=DAY_NAMES.index)
    return out


def normalize_skip_months(values: list[int]) -> list[int]:
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
