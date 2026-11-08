"""Request bodies for the HTTP API."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.config import (
    DEFAULT_BUSY_GIVE_UP_SECONDS,
    DEFAULT_BUSY_RETRY_SECONDS,
    DEFAULT_SENTENCE_PAUSE,
    DEFAULT_SPEED,
    MAX_BUSY_GIVE_UP_SECONDS,
    MAX_BUSY_RETRY_SECONDS,
    MAX_SENTENCE_PAUSE,
    MAX_SPEECH_SPEED,
    MAX_TEXT_CHARS,
    MIN_SPEECH_SPEED,
    TIMEZONE,
)
from app.models import (
    Announcement,
    BaselineSchedule,
    EmergencySchedule,
    MonthlyRangeSchedule,
    MonthlySchedule,
    RangeSchedule,
    Schedule,
    WeeklySchedule,
    normalize_announcement_text,
)
from app.users import Role


class TtsSpeechParams(BaseModel):
    speed: float = Field(default=DEFAULT_SPEED, gt=MIN_SPEECH_SPEED, le=MAX_SPEECH_SPEED)
    sentence_pause: float = Field(
        default=DEFAULT_SENTENCE_PAUSE, ge=0.0, le=MAX_SENTENCE_PAUSE
    )


class SpeakRequest(TtsSpeechParams):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str | None = None

    @field_validator("text")
    @classmethod
    def text_present(cls, value: str) -> str:
        return normalize_announcement_text(value)


class NewslinePreviewIn(BaseModel):
    opening_announcement_id: str | None = None
    station_id_announcement_id: str | None = None
    closing_announcement_id: str | None = None
    resume_announcement_id: str | None = None


class PrepareRequest(BaseModel):
    voice: str | None = None


class ScheduleInCommon(BaseModel):
    id: str | None = None
    name: str
    enabled: bool = True
    timezone: str = TIMEZONE
    announcement_id: str | None = None
    offset_minutes: int = 0
    priority: int | None = None


class BaselineScheduleIn(ScheduleInCommon):
    kind: Literal["baseline"]


class WeeklyScheduleIn(ScheduleInCommon):
    kind: Literal["weekly"]
    # Accepted as free strings; Schedule validators normalize to DayName.
    days: list[str] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)


class MonthlyScheduleIn(ScheduleInCommon):
    kind: Literal["monthly"]
    days: list[str] = Field(default_factory=list)
    occurrence: int | None = None
    skip_months: list[int] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)


class RangeScheduleIn(ScheduleInCommon):
    kind: Literal["range"]
    start_date: date | None = None
    event_date: date | None = None
    event_slot: str | None = None
    slots: list[str] = Field(default_factory=list)


class MonthlyRangeScheduleIn(ScheduleInCommon):
    kind: Literal["monthly_range"]
    days: list[str] = Field(default_factory=list)
    occurrence: int | None = None
    skip_months: list[int] = Field(default_factory=list)
    days_before: int | None = None
    event_slot: str | None = None
    slots: list[str] = Field(default_factory=list)


class EmergencyScheduleIn(ScheduleInCommon):
    kind: Literal["emergency"]


ScheduleIn = Annotated[
    BaselineScheduleIn
    | WeeklyScheduleIn
    | MonthlyScheduleIn
    | RangeScheduleIn
    | MonthlyRangeScheduleIn
    | EmergencyScheduleIn,
    Field(discriminator="kind"),
]


class SchedulePatch(BaseModel):
    enabled: bool | None = None


def _default_kind_tts(data: object) -> object:
    if isinstance(data, dict):
        payload = cast(dict[str, object], data)
        if "kind" not in payload:
            return {**payload, "kind": "tts"}
        return payload
    return data


class AnnouncementInCommon(BaseModel):
    name: str
    busy_retry_seconds: float = Field(
        default=DEFAULT_BUSY_RETRY_SECONDS, gt=0, le=MAX_BUSY_RETRY_SECONDS
    )
    busy_give_up_seconds: float = Field(
        default=DEFAULT_BUSY_GIVE_UP_SECONDS, gt=0, le=MAX_BUSY_GIVE_UP_SECONDS
    )


class TtsAnnouncementIn(AnnouncementInCommon, TtsSpeechParams):
    kind: Literal["tts"] = "tts"
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    voice: str | None = None

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def text_for_spoken(self) -> TtsAnnouncementIn:
        self.text = normalize_announcement_text(self.text)
        return self


class NewslineAnnouncementIn(AnnouncementInCommon):
    kind: Literal["newsline"]
    opening_announcement_id: str | None = None
    station_id_announcement_id: str | None = None
    closing_announcement_id: str | None = None
    resume_announcement_id: str | None = None
    ptt_gap_seconds: float = Field(default=2.0, ge=0.0, le=30.0)

    @model_validator(mode="after")
    def require_station_id(self) -> NewslineAnnouncementIn:
        if not self.station_id_announcement_id:
            raise ValueError("A station ID announcement is required")
        return self


AnnouncementIn = Annotated[
    Annotated[
        TtsAnnouncementIn | NewslineAnnouncementIn,
        Field(discriminator="kind"),
    ],
    BeforeValidator(_default_kind_tts),
]


class SettingsIn(BaseModel):
    slot_half_window_minutes: int | None = None
    baseline_randomize: bool | None = None
    news_check_start: str | None = None
    news_check_stop: str | None = None
    repeater_timeout_seconds: int | None = None


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class SetupIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    role: Role = "readonly"


class UserUpdateIn(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=1, max_length=256)
    role: Role | None = None


class AnnouncementsListOut(BaseModel):
    announcements: list[Announcement]


class BaselineScheduleDetail(BaselineSchedule):
    warning: str | None = None


class WeeklyScheduleDetail(WeeklySchedule):
    warning: str | None = None


class MonthlyScheduleDetail(MonthlySchedule):
    warning: str | None = None


class RangeScheduleDetail(RangeSchedule):
    warning: str | None = None


class MonthlyRangeScheduleDetail(MonthlyRangeSchedule):
    warning: str | None = None


class EmergencyScheduleDetail(EmergencySchedule):
    warning: str | None = None


ScheduleDetail = Annotated[
    BaselineScheduleDetail
    | WeeklyScheduleDetail
    | MonthlyScheduleDetail
    | RangeScheduleDetail
    | MonthlyRangeScheduleDetail
    | EmergencyScheduleDetail,
    Field(discriminator="kind"),
]

schedule_detail_adapter: TypeAdapter[ScheduleDetail] = TypeAdapter(ScheduleDetail)


def build_schedule_detail(schedule: Schedule, warning: str | None) -> ScheduleDetail:
    """Attach an optional home/editor warning to a persisted schedule row."""
    return schedule_detail_adapter.validate_python({**schedule.model_dump(), "warning": warning})
