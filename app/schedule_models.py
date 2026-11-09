"""Per-kind Schedule models (Pydantic v2 discriminated union on `kind`)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, ClassVar, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from app.config import TIMEZONE
from app.model_common import (
    DEFAULT_PRIORITY,
    OVERLAY_KINDS,
    DayName,
    ScheduleKind,
    format_hhmm,
    is_slot_stamp,
    new_id,
    normalize_days,
    normalize_entity_name,
    normalize_skip_months,
    normalize_slots,
    parse_hhmm,
)


class ScheduleCommon(BaseModel):
    """Fields shared by every schedule kind (discriminator lives on each variant)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    id: str = Field(default_factory=new_id)
    name: str
    enabled: bool = True
    # Stored for compatibility only. Slot math always uses the document's
    # Settings.timezone so every schedule shares one station clock.
    timezone: str = TIMEZONE
    announcement_id: str | None = None
    offset_minutes: int = 0
    priority: int = 0
    last_run_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def name_present(cls, value: str) -> str:
        return normalize_entity_name(value)

    def _use_default_priority(self, kind: ScheduleKind) -> None:
        self.priority = DEFAULT_PRIORITY[kind]

    def _clear_offset(self) -> None:
        self.offset_minutes = 0

    @model_validator(mode="after")
    def timezone_and_last_run(self) -> ScheduleCommon:
        _ = ZoneInfo(self.timezone)
        if self.last_run_at is not None and self.last_run_at.tzinfo is None:
            self.last_run_at = self.last_run_at.replace(tzinfo=ZoneInfo(self.timezone))
        return self

    @property
    def is_silence(self) -> bool:
        kind = cast(ScheduleKind, getattr(self, "kind"))
        return kind != "baseline" and not self.announcement_id

    @property
    def is_overlay(self) -> bool:
        kind = cast(ScheduleKind, getattr(self, "kind"))
        return kind in OVERLAY_KINDS


class BaselineSchedule(ScheduleCommon):
    kind: Literal["baseline"] = "baseline"

    @model_validator(mode="after")
    def kind_fields(self) -> BaselineSchedule:
        if not self.announcement_id:
            raise ValueError("A baseline schedule needs an announcement")
        self._clear_offset()
        self._use_default_priority("baseline")
        return self


class WeeklySchedule(ScheduleCommon):
    kind: Literal["weekly"] = "weekly"
    days: list[DayName] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def kind_fields(self) -> WeeklySchedule:
        if self.priority == 0:
            self._use_default_priority("weekly")
        self.days = normalize_days(list(self.days))
        self.slots = normalize_slots(self.slots, required=True)
        return self


class MonthlySchedule(ScheduleCommon):
    kind: Literal["monthly"] = "monthly"
    days: list[DayName] = Field(default_factory=list)
    occurrence: int | None = None
    skip_months: list[int] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def kind_fields(self) -> MonthlySchedule:
        self.days = normalize_days(list(self.days))
        if len(self.days) != 1:
            raise ValueError("A monthly schedule needs exactly one weekday")
        if self.occurrence not in {1, 2, 3, 4}:
            raise ValueError("Monthly occurrence must be 1st, 2nd, 3rd, or 4th")
        if self.priority == 0:
            self._use_default_priority("monthly")
        self.skip_months = normalize_skip_months(self.skip_months)
        self.slots = normalize_slots(self.slots, required=True)
        return self


class RangeSchedule(ScheduleCommon):
    kind: Literal["range"] = "range"
    start_date: date | None = None
    event_date: date | None = None
    event_slot: str | None = None
    slots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def kind_fields(self) -> RangeSchedule:
        if self.start_date is None or self.event_date is None or not self.event_slot:
            raise ValueError("An event countdown needs a start date and an event slot")
        if not is_slot_stamp(self.event_slot):
            raise ValueError("Event slot must be on the hour or half-hour")
        if self.event_date < self.start_date:
            raise ValueError("Event date cannot be before the start date")
        if self.priority == 0:
            self._use_default_priority("range")
        self.event_slot = format_hhmm(*parse_hhmm(self.event_slot))
        self.slots = normalize_slots(self.slots, required=False)
        return self


class MonthlyRangeSchedule(ScheduleCommon):
    kind: Literal["monthly_range"] = "monthly_range"
    days: list[DayName] = Field(default_factory=list)
    occurrence: int | None = None
    skip_months: list[int] = Field(default_factory=list)
    days_before: int | None = None
    event_slot: str | None = None
    slots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def kind_fields(self) -> MonthlyRangeSchedule:
        self.days = normalize_days(list(self.days))
        if len(self.days) != 1:
            raise ValueError("A monthly schedule needs exactly one weekday")
        if self.occurrence not in {1, 2, 3, 4}:
            raise ValueError("Monthly occurrence must be 1st, 2nd, 3rd, or 4th")
        if self.days_before is None:
            raise ValueError("A monthly countdown needs how many days before the event to start")
        if self.days_before < 0 or self.days_before > 90:
            raise ValueError("Days before the event must be 0–90")
        if not self.event_slot:
            raise ValueError("A monthly countdown needs an event slot")
        if not is_slot_stamp(self.event_slot):
            raise ValueError("Event slot must be on the hour or half-hour")
        if self.priority == 0:
            self._use_default_priority("monthly_range")
        self.skip_months = normalize_skip_months(self.skip_months)
        self.event_slot = format_hhmm(*parse_hhmm(self.event_slot))
        self.slots = normalize_slots(self.slots, required=False)
        return self


class EmergencySchedule(ScheduleCommon):
    kind: Literal["emergency"] = "emergency"

    @model_validator(mode="after")
    def kind_fields(self) -> EmergencySchedule:
        self._use_default_priority("emergency")
        return self


Schedule = Annotated[
    BaselineSchedule
    | WeeklySchedule
    | MonthlySchedule
    | RangeSchedule
    | MonthlyRangeSchedule
    | EmergencySchedule,
    Field(discriminator="kind"),
]

OverlaySchedule = Annotated[
    WeeklySchedule
    | MonthlySchedule
    | RangeSchedule
    | MonthlyRangeSchedule
    | EmergencySchedule,
    Field(discriminator="kind"),
]

schedule_adapter: TypeAdapter[Schedule] = TypeAdapter(Schedule)


def parse_schedule(data: object | None = None, /, **kwargs: object) -> Schedule:
    """Validate a schedule dict (or kwargs) into the discriminated union."""
    payload = kwargs if data is None else data
    return schedule_adapter.validate_python(payload)


validate_schedule = parse_schedule


def schedule_offset_minutes(schedule: Schedule) -> int:
    """Fire offset from the slot center (baselines are always on the mark)."""
    if schedule.kind == "baseline":
        return 0
    return schedule.offset_minutes
