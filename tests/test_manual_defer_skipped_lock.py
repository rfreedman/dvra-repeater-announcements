"""Manual defer must survive playback_lock contention (no silent lost queue)."""
from __future__ import annotations

from app.audio import PcmChunk
from app.scheduler import _playback_lock, get_scheduler, run_manual_trigger
from tests.test_api import _seed_baseline


def test_manual_trigger_reschedules_when_playback_lock_held(
    radio, store, client, monkeypatch
):
    monkeypatch.setattr("app.config.TRIGGER_NOW", True)
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "app.scheduler.chunks_for_announcement",
        lambda _item: [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
    )
    monkeypatch.setattr("app.scheduler.play_chunks", lambda _chunks: None)

    _announcement, schedule = _seed_baseline(store)
    radio.busy = False
    defer_id = f"defer:manual:{schedule.id}"
    sched = get_scheduler()
    assert sched is not None

    assert _playback_lock.acquire(blocking=False)
    try:
        result = run_manual_trigger(schedule.id)
        assert result == "deferred"
        assert sched.get_job(defer_id) is not None
    finally:
        _playback_lock.release()
