from __future__ import annotations

from app.audio import PcmChunk
from app.models import Announcement
from app.pcm_cache import PcmCache, chunks_for_announcement, fingerprint, set_pcm_cache


def _announcement(**kwargs) -> Announcement:
    data = {
        "id": "ann1",
        "name": "ID",
        "text": "This is w2-zee-q.",
        "voice": "en_US-lessac-medium",
        "speed": 1.0,
        "sentence_pause": 0.25,
    }
    data.update(kwargs)
    return Announcement(**data)


def test_pcm_cache_round_trip(tmp_path):
    cache = PcmCache(tmp_path / "pcm")
    expected = fingerprint("hello", "en_US-lessac-medium", 1.0, 0.25, 123)
    chunk = PcmChunk(pcm_int16=b"\x01\x00\x02\x00", sample_rate=22050)
    cache.write("ann1", expected, [chunk])
    loaded = cache.read("ann1", expected)
    assert loaded is not None
    assert loaded[0].pcm_int16 == b"\x01\x00\x02\x00"
    assert loaded[0].sample_rate == 22050


def test_pcm_cache_miss_on_fingerprint_change(tmp_path):
    cache = PcmCache(tmp_path / "pcm")
    cache.write("ann1", fingerprint("hello", "v", 1.0, 0.25, 1), [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)])
    assert cache.read("ann1", fingerprint("hello!", "v", 1.0, 0.25, 1)) is None


def test_pcm_cache_drop_and_prune(tmp_path):
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


def test_chunks_for_announcement_uses_disk_after_first_render(tmp_path, monkeypatch):
    cache = PcmCache(tmp_path / "pcm")
    set_pcm_cache(cache)
    calls: list[str] = []

    class FakeEngine:
        def voice_mtime_ns(self, voice_id: str) -> int | None:
            return 99

    class FakeRegistry:
        engine = FakeEngine()

        def prepare(self, voice=None) -> str:
            return "en_US-lessac-medium"

        def synthesize(self, text, voice=None, speed=1.0, sentence_pause=0.25):
            calls.append(text)
            return voice, 22050, [PcmChunk(pcm_int16=b"\x03\x00", sample_rate=22050)]

    monkeypatch.setattr("app.registry.get_registry", lambda: FakeRegistry())
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
