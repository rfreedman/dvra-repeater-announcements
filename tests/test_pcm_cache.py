from __future__ import annotations

import logging

from app.audio import PcmChunk
from app.models import NewslineAnnouncement, TtsAnnouncement
import pytest
from pathlib import Path
from app.store import AnnouncementStore
from app.pcm_cache import (
    PcmCache,
    chunks_for_announcement,
    fingerprint,
    set_pcm_cache,
    warm_all,
)


def _announcement(**kwargs: object) -> TtsAnnouncement:
    data: dict[str, object] = {
        "id": "ann1",
        "name": "ID",
        "text": "This is w2-zee-q.",
        "voice": "en_US-lessac-medium",
        "speed": 1.0,
        "sentence_pause": 0.25,
    }
    data.update(kwargs)
    return TtsAnnouncement.model_validate(data)


def test_pcm_cache_round_trip(tmp_path: Path) -> None:
    cache = PcmCache(tmp_path / "pcm")
    expected = fingerprint("hello", "en_US-lessac-medium", 1.0, 0.25, 123)
    chunk = PcmChunk(pcm_int16=b"\x01\x00\x02\x00", sample_rate=22050)
    cache.write("ann1", expected, [chunk])
    loaded = cache.read("ann1", expected)
    assert loaded is not None
    assert loaded[0].pcm_int16 == b"\x01\x00\x02\x00"
    assert loaded[0].sample_rate == 22050


def test_pcm_cache_miss_on_fingerprint_change(tmp_path: Path) -> None:
    cache = PcmCache(tmp_path / "pcm")
    cache.write("ann1", fingerprint("hello", "v", 1.0, 0.25, 1), [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)])
    assert cache.read("ann1", fingerprint("hello!", "v", 1.0, 0.25, 1)) is None


def test_pcm_cache_drop_and_prune(tmp_path: Path) -> None:
    cache = PcmCache(tmp_path / "pcm")
    fp = fingerprint("a", "v", 1.0, 0.25, None)
    cache.write("keep", fp, [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)])
    cache.write("gone", fp, [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)])
    cache.drop("gone")
    assert not (tmp_path / "pcm" / "gone").exists()
    cache.write("stale", fp, [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)])
    cache.prune({"keep"})
    assert (tmp_path / "pcm" / "keep").exists()
    assert not (tmp_path / "pcm" / "stale").exists()


def test_chunks_for_announcement_uses_disk_after_first_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = PcmCache(tmp_path / "pcm")
    set_pcm_cache(cache)
    calls: list[str] = []

    class FakeEngine:
        def voice_mtime_ns(self, voice_id: str) -> int | None:
            _ = voice_id
            return 99

    class FakeRegistry:
        engine: FakeEngine = FakeEngine()

        def prepare(self, voice: str | None = None) -> str:
            _ = voice
            return "en_US-lessac-medium"

        def synthesize(
            self,
            text: str,
            voice: str | None = None,
            speed: float = 1.0,
            sentence_pause: float = 0.25,
        ) -> tuple[str | None, int, list[PcmChunk]]:
            _ = speed
            _ = sentence_pause
            calls.append(text)
            return voice, 22050, [PcmChunk(pcm_int16=b"\x03\x00", sample_rate=22050)]

    def get_registry() -> FakeRegistry:
        return FakeRegistry()

    monkeypatch.setattr("app.registry.get_registry", get_registry)
    item = _announcement()
    first = chunks_for_announcement(item)
    second = chunks_for_announcement(item)
    assert first[0].pcm_int16 == b"\x03\x00"
    assert second[0].pcm_int16 == b"\x03\x00"
    assert calls == ["This is w2-zee-q."]
    item = item.model_copy(update={"text": "New copy."})
    third = chunks_for_announcement(item)
    assert calls == ["This is w2-zee-q.", "New copy."]
    assert third[0].pcm_int16 == b"\x03\x00"
    set_pcm_cache(None)


def test_cache_hit_does_not_log_caching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    cache = PcmCache(tmp_path / "pcm")
    set_pcm_cache(cache)

    class FakeEngine:
        def voice_mtime_ns(self, voice_id: str) -> int | None:
            _ = voice_id
            return 99

    class FakeRegistry:
        engine: FakeEngine = FakeEngine()

        def prepare(self, voice: str | None = None) -> str:
            _ = voice
            return "en_US-lessac-medium"

        def synthesize(
            self,
            text: str,
            voice: str | None = None,
            speed: float = 1.0,
            sentence_pause: float = 0.25,
        ) -> tuple[str | None, int, list[PcmChunk]]:
            _ = text
            _ = speed
            _ = sentence_pause
            return voice, 22050, [PcmChunk(pcm_int16=b"\x03\x00", sample_rate=22050)]

    def get_registry() -> FakeRegistry:
        return FakeRegistry()

    monkeypatch.setattr("app.registry.get_registry", get_registry)
    item = _announcement()
    _ = chunks_for_announcement(item)
    with caplog.at_level(logging.INFO, logger="app.pcm_cache"):
        _ = chunks_for_announcement(item)
    assert "PCM cache hit;" in caplog.text
    assert "Warming all TTS announcements" not in caplog.text
    assert "Caching " not in caplog.text
    set_pcm_cache(None)


def test_warm_all_counts_only_tts_with_text(store: AnnouncementStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    cache = PcmCache(tmp_path / "pcm")
    set_pcm_cache(cache)

    class FakeEngine:
        def voice_mtime_ns(self, voice_id: str) -> int | None:
            _ = voice_id
            return 99

    class FakeRegistry:
        engine: FakeEngine = FakeEngine()

        def prepare(self, voice: str | None = None) -> str:
            _ = voice
            return "en_US-lessac-medium"

        def synthesize(
            self,
            text: str,
            voice: str | None = None,
            speed: float = 1.0,
            sentence_pause: float = 0.25,
        ) -> tuple[str | None, int, list[PcmChunk]]:
            _ = text
            _ = speed
            _ = sentence_pause
            return voice, 22050, [PcmChunk(pcm_int16=b"\x03\x00", sample_rate=22050)]

    def get_registry() -> FakeRegistry:
        return FakeRegistry()

    monkeypatch.setattr("app.registry.get_registry", get_registry)
    a = store.save_announcement(TtsAnnouncement(name="A", text="Hello."))
    b = store.save_announcement(TtsAnnouncement(name="B", text="World."))
    _ = store.save_announcement(
        NewslineAnnouncement(
            name="Newsline",
            station_id_announcement_id=a.id,
        )
    )
    with caplog.at_level(logging.INFO, logger="app.pcm_cache"):
        warm_all()
    start = [r.message for r in caplog.records if "Warming all TTS announcements" in r.message]
    end = [r.message for r in caplog.records if r.message.startswith("Cached ")]
    per_item = [
        r.message
        for r in caplog.records
        if r.message.startswith("PCM cache hit;") or r.message.startswith("PCM cache stored;")
    ]
    assert start == ["Warming all TTS announcements; caching 2"]
    assert len(end) == 1 and end[0].startswith("Cached 2 announcements in ")
    assert len(per_item) == 2
    assert {a.name, b.name} == {
        message.split("; ", 1)[1].split(" in ", 1)[0] for message in per_item
    }
    set_pcm_cache(None)
