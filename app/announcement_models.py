"""Per-kind Announcement models (Pydantic v2 discriminated union on `kind`)."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
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
)
from app.model_common import new_id, normalize_announcement_text, normalize_entity_name


def _default_kind_tts(data: object) -> object:
    """Old clients and tests omit `kind`; treat that as spoken TTS."""
    if isinstance(data, dict):
        payload = cast(dict[str, object], data)
        if "kind" not in payload:
            return {**payload, "kind": "tts"}
        return payload
    return data


class AnnouncementCommon(BaseModel):
    """Fields shared by every library announcement (discriminator on each variant)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    id: str = Field(default_factory=new_id)
    name: str
    busy_retry_seconds: float = Field(
        default=DEFAULT_BUSY_RETRY_SECONDS, gt=0, le=MAX_BUSY_RETRY_SECONDS
    )
    busy_give_up_seconds: float = Field(
        default=DEFAULT_BUSY_GIVE_UP_SECONDS, gt=0, le=MAX_BUSY_GIVE_UP_SECONDS
    )

    @field_validator("name")
    @classmethod
    def name_present(cls, value: str) -> str:
        return normalize_entity_name(value)


class TtsAnnouncement(AnnouncementCommon):
    """Spoken Piper text."""

    kind: Literal["tts"] = "tts"
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    voice: str | None = None
    speed: float = Field(default=DEFAULT_SPEED, gt=MIN_SPEECH_SPEED, le=MAX_SPEECH_SPEED)
    sentence_pause: float = Field(
        default=DEFAULT_SENTENCE_PAUSE, ge=0.0, le=MAX_SENTENCE_PAUSE
    )
    play_seconds: float | None = None
    too_long: bool = False

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def kind_fields(self) -> TtsAnnouncement:
        self.text = normalize_announcement_text(self.text)  # pyright: ignore[reportUnannotatedClassAttribute]
        return self


class NewslineAnnouncement(AnnouncementCommon):
    """Newsline bulletin that plays story audio plus optional TTS bookends."""

    kind: Literal["newsline"] = "newsline"
    opening_announcement_id: str | None = None
    station_id_announcement_id: str | None = None
    closing_announcement_id: str | None = None
    resume_announcement_id: str | None = None
    ptt_gap_seconds: float = Field(default=2.0, ge=0.0, le=30.0)

    @model_validator(mode="after")
    def kind_fields(self) -> NewslineAnnouncement:
        if not self.station_id_announcement_id:
            raise ValueError("A station ID announcement is required")
        return self


Announcement = Annotated[
    Annotated[
        TtsAnnouncement | NewslineAnnouncement,
        Field(discriminator="kind"),
    ],
    BeforeValidator(_default_kind_tts),
]

announcement_adapter: TypeAdapter[Announcement] = TypeAdapter(Announcement)


def parse_announcement(data: object | None = None, /, **kwargs: object) -> Announcement:
    """Validate an announcement dict (or kwargs) into the discriminated union."""
    payload = kwargs if data is None else data
    return announcement_adapter.validate_python(payload)


validate_announcement = parse_announcement
