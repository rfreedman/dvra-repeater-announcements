from __future__ import annotations

from collections.abc import Iterator, Sequence

from app.audio import PcmChunk, materialize_chunks
from app import config as app_config
from app.config import (
    DEFAULT_SENTENCE_PAUSE,
    DEFAULT_SPEED,
    MAX_BUSY_GIVE_UP_SECONDS,
)
from app.tts.base import VoiceInfo, VoicesPayload
from app.tts.piper import PiperEngine


class EngineRegistry:
    engine: PiperEngine

    def __init__(self) -> None:
        self.engine = PiperEngine()

    def prepare(self, voice: str | None = None) -> str:
        return self.engine.prepare(voice)

    def preload(self) -> list[str]:
        return self.engine.preload()

    def stream_synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = DEFAULT_SPEED,
        sentence_pause: float = DEFAULT_SENTENCE_PAUSE,
    ) -> tuple[str, int, Iterator[PcmChunk]]:
        """Resolve/load the voice now; PCM is produced lazily, one sentence at a time."""
        voice_id = self.engine.prepare(voice)
        rate = self.engine.sample_rate(voice_id)
        chunks = self.engine.synthesize(
            text,
            voice=voice_id,
            speed=speed,
            sentence_pause=sentence_pause,
        )
        return voice_id, rate, chunks

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = DEFAULT_SPEED,
        sentence_pause: float = DEFAULT_SENTENCE_PAUSE,
    ) -> tuple[str, int, Sequence[PcmChunk]]:
        voice_id, rate, chunks = self.stream_synthesize(
            text, voice=voice, speed=speed, sentence_pause=sentence_pause
        )
        return voice_id, rate, materialize_chunks(chunks)

    def voices_payload(self) -> VoicesPayload:
        return {
            "default_voice": self.engine.default_voice_id(),
            "ready": self.engine.is_ready(),
            "speed": app_config.DEFAULT_SPEED,
            "sentence_pause": app_config.DEFAULT_SENTENCE_PAUSE,
            "busy_retry_seconds": app_config.DEFAULT_BUSY_RETRY_SECONDS,
            "busy_give_up_seconds": app_config.DEFAULT_BUSY_GIVE_UP_SECONDS,
            "max_busy_give_up_seconds": MAX_BUSY_GIVE_UP_SECONDS,
            "ptt_lead_seconds": app_config.PTT_LEAD_SECONDS,
            "max_text_chars": app_config.MAX_TEXT_CHARS,
            "station_call": app_config.STATION_CALL,
            "station_name": app_config.STATION_NAME,
            "app_title": app_config.APP_TITLE,
            "voices": [voice.to_dict() for voice in self.engine.list_voices()],
        }

    def list_voices(self) -> list[VoiceInfo]:
        return self.engine.list_voices()


def get_registry() -> EngineRegistry:
    from app.runtime import get_runtime

    runtime = get_runtime()
    registry = runtime.registry
    if isinstance(registry, EngineRegistry):
        return registry
    created = EngineRegistry()
    runtime.registry = created
    return created


def set_registry(registry: EngineRegistry | None) -> None:
    from app.runtime import get_runtime

    get_runtime().registry = registry
