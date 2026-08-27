from __future__ import annotations

from collections.abc import Iterator

from app.audio import PcmChunk
from app.config import DEFAULT_SENTENCE_PAUSE, DEFAULT_SPEED
from app.engines.base import VoiceInfo
from app.engines.piper import PiperEngine


class EngineRegistry:
    def __init__(self) -> None:
        self.engine = PiperEngine()

    def prepare(self, voice: str | None = None) -> str:
        return self.engine.prepare(voice)

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = DEFAULT_SPEED,
        sentence_pause: float = DEFAULT_SENTENCE_PAUSE,
    ) -> tuple[str, int, Iterator[PcmChunk]]:
        voice_id = self.engine.prepare(voice)
        rate = self.engine.sample_rate(voice_id)
        chunks = self.engine.synthesize(
            text,
            voice=voice_id,
            speed=speed,
            sentence_pause=sentence_pause,
        )
        return voice_id, rate, chunks

    def voices_payload(self) -> dict[str, object]:
        return {
            "default_voice": self.engine.default_voice_id(),
            "ready": self.engine.is_ready(),
            "speed": DEFAULT_SPEED,
            "sentence_pause": DEFAULT_SENTENCE_PAUSE,
            "voices": [voice.to_dict() for voice in self.engine.list_voices()],
        }

    def list_voices(self) -> list[VoiceInfo]:
        return self.engine.list_voices()


_REGISTRY: EngineRegistry | None = None


def get_registry() -> EngineRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = EngineRegistry()
    return _REGISTRY
