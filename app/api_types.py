"""Typed JSON shapes for HTTP API responses (beyond raw Pydantic models)."""

from __future__ import annotations

from typing import TypedDict

from app.manual_trigger_types import HomeLiveStatus
from app.models import DAY_LABELS, DAY_NAMES, DEFAULT_PRIORITY, MONTH_LABELS, DayName, ScheduleKind, Settings
from app.newsline.state import NewslineOutcome, NewslinePhase


class DayMeta(TypedDict):
    id: DayName
    label: str


class MonthMeta(TypedDict):
    id: int
    label: str


class ScheduleMeta(TypedDict):
    days: list[DayMeta]
    months: list[MonthMeta]
    default_priority: dict[str, int]


class SettingsPayload(TypedDict):
    timezone: str
    slot_half_window_minutes: int
    baseline_randomize: bool
    news_check_start: str
    news_check_stop: str
    repeater_timeout_seconds: int


def schedule_meta() -> ScheduleMeta:
    """Day and month labels plus template priorities for the schedule editor."""
    days: list[DayMeta] = [{"id": day, "label": DAY_LABELS[day]} for day in DAY_NAMES]
    months: list[MonthMeta] = [
        {"id": month, "label": MONTH_LABELS[month]} for month in range(1, 13)
    ]
    return {
        "days": days,
        "months": months,
        "default_priority": dict(DEFAULT_PRIORITY),
    }


def settings_payload(settings: Settings) -> SettingsPayload:
    return {
        "timezone": settings.timezone,
        "slot_half_window_minutes": settings.slot_half_window_minutes,
        "baseline_randomize": settings.baseline_randomize,
        "news_check_start": settings.news_check_start,
        "news_check_stop": settings.news_check_stop,
        "repeater_timeout_seconds": settings.repeater_timeout_seconds,
    }


class ScheduleRow(TypedDict):
    schedule_id: str
    name: str
    kind: ScheduleKind
    enabled: bool
    announcement_id: str | None
    announcement_name: str | None
    summary: str
    warning: str | None
    priority: int
    offset_minutes: int
    last_run_at: str | None
    next_run_at: str | None


class ClockRow(TypedDict):
    slot: str
    slot_at: str
    fire_at: str
    source: str
    schedule_id: str | None
    schedule_name: str | None
    announcement_id: str | None
    announcement_name: str | None
    label: str
    warning: str | None


class NewslineStatus(TypedDict):
    report_number: int | None
    release_friday: str | None
    split_ok: bool
    ready: bool
    phase: NewslinePhase
    error: str | None
    completed_at: str | None
    last_check_at: str | None
    in_progress: bool
    playing: bool
    working_report: int | None
    outcome: NewslineOutcome | None
    step: str | None
    elapsed_seconds: float | None


class NewslineProgress(TypedDict):
    step: str | None
    elapsed_seconds: float | None


class SchedulesListPayload(HomeLiveStatus):
    warnings: list[str]
    settings: SettingsPayload
    clock_date: str
    clock: list[ClockRow]
    schedules: list[ScheduleRow]
    trigger_now_enabled: bool
    newsline: NewslineStatus


class TriggerResult(TypedDict):
    result: str


__all__ = [
    "ClockRow",
    "DayMeta",
    "MonthMeta",
    "NewslineProgress",
    "NewslineStatus",
    "ScheduleMeta",
    "ScheduleRow",
    "SchedulesListPayload",
    "SettingsPayload",
    "TriggerResult",
    "schedule_meta",
    "settings_payload",
]
