from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from piper import PiperVoice, SynthesisConfig
from piper.download_voices import VOICE_PATTERN, download_voice

from app.audio import PauseSegment, PcmChunk, parse_script, silence_chunk, with_sentence_pauses
from app.config import DEFAULT_PIPER_VOICE, PIPER_VOICES_DIR, PIPER_VOICES_FILE, piper_quality_allowed
from app.engines.base import Engine, VoiceError, VoiceInfo

log = logging.getLogger("app.engines.piper")

_REQUIRED_FIELDS = ("id", "alias", "name", "gender", "locale", "quality", "description")
_KNOWN_QUALITIES = ("x_low", "low", "medium", "high")


@dataclass(frozen=True)
class PiperVoiceMeta:
    id: str
    alias: str
    name: str
    gender: str
    locale: str
    quality: str
    description: str
    onnx_url: str | None = None
    config_url: str | None = None


def _optional_http_url(value: object, *, field: str, voices_path: Path, index: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"{voices_path} voices[{index}].{field} must be an http(s) URL, got {text!r}"
        )
    return text


def load_featured_voices(path: Path | None = None) -> tuple[PiperVoiceMeta, ...]:
    """Load featured voices from JSON. Raises ValueError on missing/invalid file."""
    voices_path = Path(path or PIPER_VOICES_FILE)
    if not voices_path.is_file():
        raise ValueError(f"Piper voices file not found: {voices_path}")
    try:
        raw = json.loads(voices_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {voices_path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("voices"), list):
        raise ValueError(f"{voices_path} must be an object with a 'voices' array")

    voices: list[PiperVoiceMeta] = []
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()
    for index, item in enumerate(raw["voices"]):
        if not isinstance(item, dict):
            raise ValueError(f"{voices_path} voices[{index}] must be an object")
        missing = [field for field in _REQUIRED_FIELDS if not str(item.get(field, "")).strip()]
        if missing:
            raise ValueError(
                f"{voices_path} voices[{index}] missing required fields: {', '.join(missing)}"
            )
        voice_id = str(item["id"]).strip()
        alias = str(item["alias"]).strip()
        alias_key = alias.lower()
        if voice_id in seen_ids:
            raise ValueError(f"{voices_path}: duplicate voice id {voice_id!r}")
        if alias_key in seen_aliases:
            raise ValueError(f"{voices_path}: duplicate voice alias {alias!r}")
        onnx_url = _optional_http_url(item.get("onnx_url"), field="onnx_url", voices_path=voices_path, index=index)
        config_url = _optional_http_url(
            item.get("config_url"), field="config_url", voices_path=voices_path, index=index
        )
        if config_url and not onnx_url:
            raise ValueError(
                f"{voices_path} voices[{index}]: config_url requires onnx_url"
            )
        seen_ids.add(voice_id)
        seen_aliases.add(alias_key)
        voices.append(
            PiperVoiceMeta(
                id=voice_id,
                alias=alias,
                name=str(item["name"]).strip(),
                gender=str(item["gender"]).strip(),
                locale=str(item["locale"]).strip(),
                quality=str(item["quality"]).strip(),
                description=str(item["description"]).strip(),
                onnx_url=onnx_url,
                config_url=config_url,
            )
        )
    return tuple(voices)


FEATURED_VOICES: tuple[PiperVoiceMeta, ...] = load_featured_voices()
_FEATURED_BY_ID = {voice.id: voice for voice in FEATURED_VOICES}
_FEATURED_BY_ALIAS = {voice.alias.lower(): voice for voice in FEATURED_VOICES}


def _quality_from_id(voice_id: str) -> str:
    for quality in _KNOWN_QUALITIES:
        if voice_id.endswith(f"-{quality}"):
            return quality
    return "custom"


def set_featured_voices(voices: tuple[PiperVoiceMeta, ...]) -> None:
    """Replace featured voices (tests)."""
    global FEATURED_VOICES, _FEATURED_BY_ID, _FEATURED_BY_ALIAS
    FEATURED_VOICES = voices
    _FEATURED_BY_ID = {voice.id: voice for voice in FEATURED_VOICES}
    _FEATURED_BY_ALIAS = {voice.alias.lower(): voice for voice in FEATURED_VOICES}


def _download_url_to_file(url: str, dest: Path) -> None:
    """Download url to dest via a temp file, then atomically replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        tmp.write_bytes(data)
        tmp.replace(dest)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise VoiceError(f"Failed to download {url} → {dest.name}: {exc}") from exc


def _ensure_featured_files(meta: PiperVoiceMeta, voices_dir: Path) -> None:
    model_path = voices_dir / f"{meta.id}.onnx"
    config_path = voices_dir / f"{meta.id}.onnx.json"
    if not model_path.exists():
        assert meta.onnx_url is not None
        log.info("Downloading %s from %s", meta.id, meta.onnx_url)
        _download_url_to_file(meta.onnx_url, model_path)
    if meta.config_url and not config_path.exists():
        log.info("Downloading %s config from %s", meta.id, meta.config_url)
        _download_url_to_file(meta.config_url, config_path)


class PiperEngine(Engine):
    id = "piper"
    name = "Piper"

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
            if not piper_quality_allowed(meta.id):
                continue
            listed_ids.add(meta.id)
            voices.append(self._info_from_meta(meta))
        if self.voices_dir.exists():
            for model_path in sorted(self.voices_dir.glob("*.onnx")):
                voice_id = model_path.stem
                if voice_id in listed_ids or not piper_quality_allowed(voice_id):
                    continue
                voices.append(
                    VoiceInfo(
                        id=voice_id,
                        name=voice_id,
                        engine=self.id,
                        gender="unknown",
                        locale=voice_id.split("-", 1)[0],
                        quality=_quality_from_id(voice_id),
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
        ids = self._preload_ids()
        loaded: list[str] = []
        started = time.perf_counter()
        log.info("Preloading %s voices", len(ids))
        for voice_id in ids:
            voice_started = time.perf_counter()
            log.info("Preloading %s", voice_id)
            try:
                self._load(voice_id)
                self._warmup(voice_id)
                loaded.append(voice_id)
                log.info("Preloaded %s in %.1fs", voice_id, time.perf_counter() - voice_started)
            except Exception:
                log.exception(
                    "Failed to preload %s in %.1fs",
                    voice_id,
                    time.perf_counter() - voice_started,
                )
        log.info("Preloaded %s of %s voices in %.1fs", len(loaded), len(ids), time.perf_counter() - started)
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

    def _info_from_meta(self, meta: PiperVoiceMeta) -> VoiceInfo:
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
        ids = [meta.id for meta in FEATURED_VOICES if piper_quality_allowed(meta.id)]
        seen = set(ids)
        if self.voices_dir.exists():
            for model_path in sorted(self.voices_dir.glob("*.onnx")):
                voice_id = model_path.stem
                if voice_id in seen or not piper_quality_allowed(voice_id):
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
                meta = _FEATURED_BY_ID.get(voice_id)
                if meta is not None and meta.onnx_url:
                    _ensure_featured_files(meta, self.voices_dir)
                elif VOICE_PATTERN.match(voice_id) is not None:
                    download_voice(voice_id, self.voices_dir)
                else:
                    raise VoiceError(
                        f"Piper voice {voice_id!r} is not on disk and has no download source "
                        "(add onnx_url in config/piper-voices.json, or use a standard Piper id)"
                    )
            model = PiperVoice.load(model_path)
            with self._global:
                cached = self._models.get(voice_id)
                if cached is not None:
                    return cached
                self._models[voice_id] = model
            return model
