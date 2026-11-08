from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import (
    DEFAULT_NEWS_CHECK_START,
    DEFAULT_NEWS_CHECK_STOP,
    DEFAULT_REPEATER_TIMEOUT_SECONDS,
    DEFAULT_SLOT_HALF_WINDOW_MINUTES,
    MAX_REPEATER_TIMEOUT_SECONDS,
    MIN_REPEATER_TIMEOUT_SECONDS,
    TIMEZONE,
)
from app.model_common import (
    DAY_LABELS as DAY_LABELS,
    DAY_NAMES as DAY_NAMES,
    DEFAULT_PRIORITY as DEFAULT_PRIORITY,
    MONTH_LABELS as MONTH_LABELS,
    OVERLAY_KINDS as OVERLAY_KINDS,
    SLOT_MINUTES as SLOT_MINUTES,
    AnnouncementKind as AnnouncementKind,
    DayName as DayName,
    ScheduleKind as ScheduleKind,
    format_hhmm as format_hhmm,
    is_slot_stamp as is_slot_stamp,
    new_id as new_id,
    normalize_announcement_text as normalize_announcement_text,
    normalize_days as normalize_days,
    normalize_entity_name as normalize_entity_name,
    normalize_slots as normalize_slots,
    parse_hhmm as parse_hhmm,
)
from app.announcement_models import (
    Announcement as Announcement,
    NewslineAnnouncement as NewslineAnnouncement,
    TtsAnnouncement as TtsAnnouncement,
    parse_announcement as parse_announcement,
    validate_announcement as validate_announcement,
)
from app.schedule_models import (
    BaselineSchedule as BaselineSchedule,
    EmergencySchedule as EmergencySchedule,
    MonthlyRangeSchedule as MonthlyRangeSchedule,
    MonthlySchedule as MonthlySchedule,
    OverlaySchedule as OverlaySchedule,
    RangeSchedule as RangeSchedule,
    Schedule as Schedule,
    WeeklySchedule as WeeklySchedule,
    parse_schedule as parse_schedule,
    schedule_offset_minutes as schedule_offset_minutes,
    validate_schedule as validate_schedule,
)


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
                + f"{MAX_REPEATER_TIMEOUT_SECONDS} seconds"
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
