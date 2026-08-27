from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

from app.audio import PcmChunk


class VoiceError(ValueError):
    """Unknown engine or voice selection."""


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    name: str
    engine: str
    gender: str
    locale: str
    quality: str
    description: str
    downloaded: bool
    default: bool = False
    alias: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "engine": self.engine,
            "gender": self.gender,
            "locale": self.locale,
            "quality": self.quality,
            "description": self.description,
            "downloaded": self.downloaded,
            "default": self.default,
            "alias": self.alias,
        }


class Engine(ABC):
    id: str
    name: str
    blurb: str

    @abstractmethod
    def default_voice_id(self) -> str: ...

    @abstractmethod
    def list_voices(self) -> list[VoiceInfo]: ...

    @abstractmethod
    def try_resolve(self, voice: str | None) -> str | None: ...

    @abstractmethod
    def is_ready(self, voice: str | None = None) -> bool: ...

    @abstractmethod
    def prepare(self, voice: str | None = None) -> str: ...

    @abstractmethod
    def sample_rate(self, voice: str | None = None) -> int: ...

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
    ) -> Iterator[PcmChunk]: ...

    def resolve(self, voice: str | None) -> str:
        resolved = self.try_resolve(voice)
        if resolved is None:
            requested = voice or "(default)"
            raise VoiceError(f"Unknown {self.name} voice: {requested}")
        return resolved
