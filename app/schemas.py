"""Request bodies for the HTTP API."""

from __future__ import annotations

from datetime import date

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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
from app.models import ScheduleKind, normalize_announcement_text
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


class PrepareRequest(BaseModel):
    voice: str | None = None


class ScheduleIn(BaseModel):
    id: str | None = None
    name: str
    enabled: bool = True
    kind: ScheduleKind
    timezone: str = TIMEZONE
    announcement_id: str | None = None
    offset_minutes: int = 0
    slots: list[str] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    occurrence: int | None = None
    skip_months: list[int] = Field(default_factory=list)
    start_date: date | None = None
    event_date: date | None = None
    days_before: int | None = None
    event_slot: str | None = None
    priority: int | None = None


class SchedulePatch(BaseModel):
    enabled: bool | None = None


class AnnouncementIn(TtsSpeechParams):
    name: str
    kind: Literal["tts", "newsline"] = "tts"
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    voice: str | None = None
    busy_retry_seconds: float = Field(
        default=DEFAULT_BUSY_RETRY_SECONDS, gt=0, le=MAX_BUSY_RETRY_SECONDS
    )
    busy_give_up_seconds: float = Field(
        default=DEFAULT_BUSY_GIVE_UP_SECONDS, gt=0, le=MAX_BUSY_GIVE_UP_SECONDS
    )
    opening_announcement_id: str | None = None
    station_id_announcement_id: str | None = None
    closing_announcement_id: str | None = None
    ptt_gap_seconds: float = Field(default=2.0, ge=0.0, le=30.0)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def text_for_spoken(self) -> AnnouncementIn:
        if self.kind == "tts":
            self.text = normalize_announcement_text(self.text)
        else:
            self.text = ""
            if not self.station_id_announcement_id:
                raise ValueError("A station ID announcement is required")
        return self


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
