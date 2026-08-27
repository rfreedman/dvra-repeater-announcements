from __future__ import annotations

from collections.abc import Iterator

from app.audio import PcmChunk
from app.config import DEFAULT_ENGINE, DEFAULT_SENTENCE_PAUSE, DEFAULT_SPEED
from app.engines.base import Engine, VoiceError, VoiceInfo
from app.engines.kitten import KittenEngine
from app.engines.piper import PiperEngine


class EngineRegistry:
    def __init__(self) -> None:
        piper = PiperEngine()
        kitten = KittenEngine()
        self._engines: dict[str, Engine] = {
            piper.id: piper,
            kitten.id: kitten,
        }
        self.default_engine_id = (
            DEFAULT_ENGINE if DEFAULT_ENGINE in self._engines else piper.id
        )

    def list_engines(self) -> list[Engine]:
        return list(self._engines.values())

    def get(self, engine_id: str | None) -> Engine:
        key = (engine_id or self.default_engine_id).strip().lower()
        key = {"kittentts": "kitten", "piper-tts": "piper"}.get(key, key)
        engine = self._engines.get(key)
        if engine is None:
            known = ", ".join(self._engines)
            raise VoiceError(f"Unknown engine {engine_id!r}. Choose one of: {known}")
        return engine

    def resolve(
        self,
        engine_id: str | None,
        voice: str | None,
    ) -> tuple[Engine, str]:
        if engine_id:
            engine = self.get(engine_id)
            return engine, engine.resolve(voice)

        requested = (voice or "").strip()
        if requested:
            matches = [
                (engine, resolved)
                for engine in self._engines.values()
                if (resolved := engine.try_resolve(requested)) is not None
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                options = ", ".join(
                    f"{engine.id}:{resolved}" for engine, resolved in matches
                )
                raise VoiceError(
                    f"Voice {requested!r} is ambiguous ({options}). "
                    "Pass --engine or an engine field in the request."
                )
            raise VoiceError(f"Unknown voice: {requested}")

        engine = self.get(self.default_engine_id)
        return engine, engine.default_voice_id()

    def prepare(self, engine_id: str | None, voice: str | None) -> tuple[str, str]:
        engine, voice_id = self.resolve(engine_id, voice)
        engine.prepare(voice_id)
        return engine.id, voice_id

    def synthesize(
        self,
        text: str,
        engine_id: str | None = None,
        voice: str | None = None,
        speed: float = DEFAULT_SPEED,
        sentence_pause: float = DEFAULT_SENTENCE_PAUSE,
    ) -> tuple[Engine, str, int, Iterator[PcmChunk]]:
        engine, voice_id = self.resolve(engine_id, voice)
        engine.prepare(voice_id)
        rate = engine.sample_rate(voice_id)
        chunks = engine.synthesize(
            text,
            voice=voice_id,
            speed=speed,
            sentence_pause=sentence_pause,
        )
        return engine, voice_id, rate, chunks

    def voices_payload(self) -> dict[str, object]:
        engines = []
        for engine in self.list_engines():
            engines.append(
                {
                    "id": engine.id,
                    "name": engine.name,
                    "blurb": engine.blurb,
                    "default_voice": engine.default_voice_id(),
                    "ready": engine.is_ready(),
                    "default": engine.id == self.default_engine_id,
                    "voices": [voice.to_dict() for voice in engine.list_voices()],
                }
            )
        default_engine = self.get(self.default_engine_id)
        return {
            "default_engine": default_engine.id,
            "default_voice": default_engine.default_voice_id(),
            "speed": DEFAULT_SPEED,
            "sentence_pause": DEFAULT_SENTENCE_PAUSE,
            "engines": engines,
        }

    def all_voices(self) -> list[VoiceInfo]:
        voices: list[VoiceInfo] = []
        for engine in self.list_engines():
            voices.extend(engine.list_voices())
        return voices


_REGISTRY: EngineRegistry | None = None


def get_registry() -> EngineRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = EngineRegistry()
    return _REGISTRY
