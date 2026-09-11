from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
from pathlib import Path

from app.audio import PcmChunk
from app.config import PCM_CACHE_DIR
from app.models import Announcement

log = logging.getLogger("app.pcm_cache")
_lock = threading.Lock()
_cache: PcmCache | None = None


def fingerprint(
    text: str,
    voice_id: str,
    speed: float,
    sentence_pause: float,
    voice_mtime_ns: int | None,
) -> str:
    payload = {
        "text": text,
        "voice": voice_id,
        "speed": speed,
        "sentence_pause": sentence_pause,
        "voice_mtime_ns": voice_mtime_ns,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def concat_chunks(chunks: list[PcmChunk]) -> PcmChunk:
    if not chunks:
        return PcmChunk(pcm_int16=b"", sample_rate=22050)
    first = chunks[0]
    return PcmChunk(
        pcm_int16=b"".join(chunk.pcm_int16 for chunk in chunks),
        sample_rate=first.sample_rate,
        sample_width=first.sample_width,
        channels=first.channels,
    )


class PcmCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, announcement_id: str) -> Path:
        return self.root / announcement_id

    def read(self, announcement_id: str, expected: str) -> list[PcmChunk] | None:
        folder = self._dir(announcement_id)
        meta_path = folder / "meta.json"
        pcm_path = folder / "audio.s16"
        if not meta_path.exists() or not pcm_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if meta.get("fingerprint") != expected:
            return None
        try:
            pcm = pcm_path.read_bytes()
        except OSError:
            return None
        return [
            PcmChunk(
                pcm_int16=pcm,
                sample_rate=int(meta.get("sample_rate") or 22050),
                sample_width=int(meta.get("sample_width") or 2),
                channels=int(meta.get("channels") or 1),
            )
        ]

    def write(self, announcement_id: str, expected: str, chunks: list[PcmChunk]) -> None:
        folder = self._dir(announcement_id)
        folder.mkdir(parents=True, exist_ok=True)
        combined = concat_chunks(list(chunks))
        meta = {
            "fingerprint": expected,
            "sample_rate": combined.sample_rate,
            "sample_width": combined.sample_width,
            "channels": combined.channels,
        }
        pcm_tmp = folder / "audio.s16.tmp"
        meta_tmp = folder / "meta.json.tmp"
        pcm_tmp.write_bytes(combined.pcm_int16)
        meta_tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        pcm_tmp.replace(folder / "audio.s16")
        meta_tmp.replace(folder / "meta.json")

    def drop(self, announcement_id: str) -> None:
        folder = self._dir(announcement_id)
        if folder.exists():
            shutil.rmtree(folder)

    def prune(self, keep_ids: set[str]) -> None:
        if not self.root.exists():
            return
        for folder in self.root.iterdir():
            if folder.is_dir() and folder.name not in keep_ids:
                shutil.rmtree(folder)


def get_pcm_cache() -> PcmCache:
    global _cache
    if _cache is None:
        _cache = PcmCache(PCM_CACHE_DIR)
    return _cache


def set_pcm_cache(cache: PcmCache | None) -> None:
    global _cache
    _cache = cache


def _fingerprint_for(announcement: Announcement, voice_id: str, voice_mtime_ns: int | None) -> str:
    return fingerprint(
        announcement.text,
        voice_id,
        announcement.speed,
        announcement.sentence_pause,
        voice_mtime_ns,
    )


def chunks_for_announcement(announcement: Announcement) -> list[PcmChunk]:
    from app.registry import get_registry

    registry = get_registry()
    voice_id = registry.prepare(announcement.voice)
    voice_mtime_ns = registry.engine.voice_mtime_ns(voice_id)
    expected = _fingerprint_for(announcement, voice_id, voice_mtime_ns)
    cache = get_pcm_cache()
    with _lock:
        hit = cache.read(announcement.id, expected)
        if hit is not None:
            log.info("PCM cache hit; %s", announcement.name)
            return hit
        _voice, _rate, chunks = registry.synthesize(
            announcement.text,
            voice=voice_id,
            speed=announcement.speed,
            sentence_pause=announcement.sentence_pause,
        )
        rendered = list(chunks)
        cache.write(announcement.id, expected, rendered)
        log.info("PCM cache stored; %s", announcement.name)
        return rendered


def warm_announcement(announcement: Announcement) -> None:
    try:
        chunks_for_announcement(announcement)
    except Exception:
        log.exception("Failed to cache PCM for %s", announcement.name)


def warm_all(announcements: list[Announcement] | None = None) -> None:
    from app.store import get_store

    items = list(announcements if announcements is not None else get_store().list_announcements())
    get_pcm_cache().prune({item.id for item in items})
    for item in items:
        warm_announcement(item)
