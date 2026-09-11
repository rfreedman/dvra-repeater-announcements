from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from piper import PiperVoice, SynthesisConfig
from piper.download_voices import VOICE_PATTERN, download_voice

from app.audio import PauseSegment, PcmChunk, parse_script, silence_chunk, with_sentence_pauses
from app.config import DEFAULT_PIPER_VOICE, PIPER_VOICES_DIR
from app.engines.base import Engine, VoiceError, VoiceInfo


@dataclass(frozen=True)
class _PiperVoiceMeta:
    id: str
    alias: str
    name: str
    gender: str
    locale: str
    quality: str
    description: str


FEATURED_VOICES: tuple[_PiperVoiceMeta, ...] = (
    _PiperVoiceMeta(
        "en_US-lessac-medium",
        "lessac",
        "Lessac",
        "female",
        "en_US",
        "medium",
        "Clear American English. Default — fast and natural enough for most use.",
    ),
    _PiperVoiceMeta(
        "en_US-amy-medium",
        "amy",
        "Amy",
        "female",
        "en_US",
        "medium",
        "Warm American English female voice.",
    ),
    _PiperVoiceMeta(
        "en_US-kristin-medium",
        "kristin",
        "Kristin",
        "female",
        "en_US",
        "medium",
        "Conversational American English female voice.",
    ),
    _PiperVoiceMeta(
        "en_US-hfc_female-medium",
        "hfc-female",
        "HFC Female",
        "female",
        "en_US",
        "medium",
        "Bright American English female voice.",
    ),
    _PiperVoiceMeta(
        "en_US-joe-medium",
        "joe",
        "Joe",
        "male",
        "en_US",
        "medium",
        "Straightforward American English male voice.",
    ),
    _PiperVoiceMeta(
        "en_US-ryan-medium",
        "ryan",
        "Ryan",
        "male",
        "en_US",
        "medium",
        "American English male voice, good speed/quality balance.",
    ),
    _PiperVoiceMeta(
        "en_US-hfc_male-medium",
        "hfc-male",
        "HFC Male",
        "male",
        "en_US",
        "medium",
        "American English male voice with a bit more character.",
    ),
    _PiperVoiceMeta(
        "en_GB-cori-medium",
        "cori",
        "Cori",
        "female",
        "en_GB",
        "medium",
        "British English female voice.",
    ),
    _PiperVoiceMeta(
        "en_GB-alan-medium",
        "alan",
        "Alan",
        "male",
        "en_GB",
        "medium",
        "British English male voice.",
    ),
)

_FEATURED_BY_ID = {voice.id: voice for voice in FEATURED_VOICES}
_FEATURED_BY_ALIAS = {voice.alias.lower(): voice for voice in FEATURED_VOICES}
log = logging.getLogger("app.engines.piper")


class PiperEngine(Engine):
    id = "piper"
    name = "Piper"
    blurb = "Faster neural TTS with sentence streaming. Best choice for Raspberry Pi."

    def __init__(self, voices_dir: Path | None = None, default_voice: str | None = None) -> None:
        self.voices_dir = Path(voices_dir or PIPER_VOICES_DIR)
        self._default_voice = default_voice or DEFAULT_PIPER_VOICE
        self._models: dict[str, PiperVoice] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global = threading.Lock()

    def default_voice_id(self) -> str:
        return self._default_voice

    def list_voices(self) -> list[VoiceInfo]:
        listed_ids: set[str] = set()
        voices: list[VoiceInfo] = []
        for meta in FEATURED_VOICES:
            listed_ids.add(meta.id)
            voices.append(self._info_from_meta(meta))
        if self.voices_dir.exists():
            for model_path in sorted(self.voices_dir.glob("*.onnx")):
                voice_id = model_path.stem
                if voice_id in listed_ids or voice_id.endswith("-high"):
                    continue
                voices.append(
                    VoiceInfo(
                        id=voice_id,
                        name=voice_id,
                        engine=self.id,
                        gender="unknown",
                        locale=voice_id.split("-", 1)[0],
                        quality="custom",
                        description="Local Piper voice model.",
                        downloaded=True,
                        default=voice_id == self._default_voice,
                    )
                )
        return voices

    def try_resolve(self, voice: str | None) -> str | None:
        if not voice:
            return self._default_voice
        key = voice.strip()
        if not key:
            return self._default_voice
        lowered = key.lower()
        if lowered in _FEATURED_BY_ALIAS:
            return _FEATURED_BY_ALIAS[lowered].id
        if key in _FEATURED_BY_ID:
            return key
        if VOICE_PATTERN.match(key):
            return key
        model_path = self.voices_dir / f"{key}.onnx"
        if model_path.exists():
            return key
        return None

    def is_ready(self, voice: str | None = None) -> bool:
        voice_id = self.resolve(voice)
        return self._model_path(voice_id).exists()

    def prepare(self, voice: str | None = None) -> str:
        voice_id = self.resolve(voice)
        self._load(voice_id)
        return voice_id

    def preload(self) -> list[str]:
        loaded: list[str] = []
        for voice_id in self._preload_ids():
            try:
                self._load(voice_id)
                self._warmup(voice_id)
                loaded.append(voice_id)
                log.info("Preloaded %s", voice_id)
            except Exception:
                log.exception("Failed to preload %s", voice_id)
        return loaded

    def sample_rate(self, voice: str | None = None) -> int:
        voice_id = self.prepare(voice)
        return self._load(voice_id).config.sample_rate

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        sentence_pause: float = 0.0,
    ) -> Iterator[PcmChunk]:
        voice_id = self.prepare(voice)
        model = self._load(voice_id)
        length_scale = 1.0 / speed if speed > 0 else 1.0
        syn_config = SynthesisConfig(length_scale=length_scale)
        lock = self._lock_for(voice_id)
        sample_rate = model.config.sample_rate

        def speech_chunks(speech: str) -> Iterator[PcmChunk]:
            with lock:
                for chunk in model.synthesize(speech, syn_config=syn_config):
                    yield PcmChunk(
                        pcm_int16=chunk.audio_int16_bytes,
                        sample_rate=chunk.sample_rate,
                        sample_width=chunk.sample_width,
                        channels=chunk.sample_channels,
                    )

        for segment in parse_script(text):
            if isinstance(segment, PauseSegment):
                if segment.seconds > 0:
                    yield silence_chunk(segment.seconds, sample_rate)
                continue
            yield from with_sentence_pauses(speech_chunks(segment.text), sentence_pause)

    def _info_from_meta(self, meta: _PiperVoiceMeta) -> VoiceInfo:
        return VoiceInfo(
            id=meta.id,
            name=meta.name,
            engine=self.id,
            gender=meta.gender,
            locale=meta.locale,
            quality=meta.quality,
            description=meta.description,
            downloaded=self._model_path(meta.id).exists(),
            default=meta.id == self._default_voice,
            alias=meta.alias,
        )

    def _model_path(self, voice_id: str) -> Path:
        return self.voices_dir / f"{voice_id}.onnx"

    def voice_mtime_ns(self, voice_id: str) -> int | None:
        path = self._model_path(voice_id)
        if not path.exists():
            return None
        return path.stat().st_mtime_ns

    def _preload_ids(self) -> list[str]:
        ids = [meta.id for meta in FEATURED_VOICES]
        seen = set(ids)
        if self.voices_dir.exists():
            for model_path in sorted(self.voices_dir.glob("*.onnx")):
                voice_id = model_path.stem
                if voice_id.endswith("-high") or voice_id in seen:
                    continue
                ids.append(voice_id)
                seen.add(voice_id)
        return ids

    def _warmup(self, voice_id: str) -> None:
        model = self._load(voice_id)
        lock = self._lock_for(voice_id)
        with lock:
            for _chunk in model.synthesize(".", syn_config=SynthesisConfig()):
                pass

    def _lock_for(self, voice_id: str) -> threading.Lock:
        with self._global:
            if voice_id not in self._locks:
                self._locks[voice_id] = threading.Lock()
            return self._locks[voice_id]

    def _load(self, voice_id: str) -> PiperVoice:
        lock = self._lock_for(voice_id)
        with lock:
            with self._global:
                cached = self._models.get(voice_id)
                if cached is not None:
                    return cached
            self.voices_dir.mkdir(parents=True, exist_ok=True)
            model_path = self._model_path(voice_id)
            if not model_path.exists():
                if VOICE_PATTERN.match(voice_id) is None:
                    raise VoiceError(f"Unknown Piper voice: {voice_id}")
                download_voice(voice_id, self.voices_dir)
            model = PiperVoice.load(model_path)
            with self._global:
                cached = self._models.get(voice_id)
                if cached is not None:
                    return cached
                self._models[voice_id] = model
            return model
