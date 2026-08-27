from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterator

import numpy as np

from app.audio import PcmChunk, float_to_pcm16, split_sentences, with_sentence_pauses
from app.config import DEFAULT_KITTEN_VOICE, KITTEN_CACHE_DIR, KITTEN_MODEL
from app.engines.base import Engine, VoiceInfo

KITTEN_SAMPLE_RATE = 24000

KITTEN_VOICES: tuple[tuple[str, str, str], ...] = (
    ("Bella", "female", "Bright, youthful American English."),
    ("Jasper", "male", "Calm American English narrator."),
    ("Luna", "female", "Soft, even American English."),
    ("Bruno", "male", "Warm, lower American English."),
    ("Rosie", "female", "Friendly conversational American English."),
    ("Hugo", "male", "Clear American English with a bit more weight."),
    ("Kiki", "female", "Light, expressive American English."),
    ("Leo", "male", "Direct American English male voice."),
)

_VOICES_BY_NAME = {name: (name, gender, description) for name, gender, description in KITTEN_VOICES}


class KittenEngine(Engine):
    id = "kitten"
    name = "KittenTTS"
    blurb = "More natural English in one small model. Slower than Piper, especially on a Pi."

    def __init__(
        self,
        cache_dir: Path | None = None,
        model_name: str | None = None,
        default_voice: str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir or KITTEN_CACHE_DIR)
        self.model_name = model_name or KITTEN_MODEL
        self._default_voice = default_voice or DEFAULT_KITTEN_VOICE
        self._model = None
        self._lock = threading.Lock()

    def default_voice_id(self) -> str:
        return self._default_voice

    def list_voices(self) -> list[VoiceInfo]:
        ready = self.is_ready()
        return [
            VoiceInfo(
                id=name,
                name=name,
                engine=self.id,
                gender=gender,
                locale="en_US",
                quality="neural",
                description=description,
                downloaded=ready,
                default=name == self._default_voice,
                alias=name.lower(),
            )
            for name, gender, description in KITTEN_VOICES
        ]

    def try_resolve(self, voice: str | None) -> str | None:
        if not voice:
            return self._default_voice
        key = voice.strip()
        if not key:
            return self._default_voice
        if key in _VOICES_BY_NAME:
            return key
        lowered = key.lower()
        for name in _VOICES_BY_NAME:
            if name.lower() == lowered:
                return name
        if key.startswith("expr-voice-"):
            return key
        return None

    def is_ready(self, voice: str | None = None) -> bool:
        if self._model is not None:
            return True
        if not self.cache_dir.exists():
            return False
        return any(self.cache_dir.rglob("*.onnx"))

    def prepare(self, voice: str | None = None) -> str:
        voice_id = self.resolve(voice)
        self._load()
        return voice_id

    def sample_rate(self, voice: str | None = None) -> int:
        return KITTEN_SAMPLE_RATE

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        sentence_pause: float = 0.0,
    ) -> Iterator[PcmChunk]:
        voice_id = self.prepare(voice)
        model = self._load()
        sentences = split_sentences(text)

        def chunks() -> Iterator[PcmChunk]:
            with self._lock:
                for sentence in sentences:
                    audio = model.generate(
                        sentence,
                        voice=voice_id,
                        speed=speed,
                        clean_text=True,
                    )
                    pcm = float_to_pcm16(np.asarray(audio))
                    if not pcm:
                        continue
                    yield PcmChunk(pcm_int16=pcm, sample_rate=KITTEN_SAMPLE_RATE)

        yield from with_sentence_pauses(chunks(), sentence_pause)

    def _load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from kittentts import KittenTTS

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._model = KittenTTS(self.model_name, cache_dir=str(self.cache_dir))
            return self._model
