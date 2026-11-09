"""Manual defer must survive playback_lock contention (no silent lost queue)."""
from __future__ import annotations
from collections.abc import Iterable
from app.radio import StubRadio

from app.audio import PcmChunk
from app.models import Announcement
from app.scheduler import get_scheduler, playback_lock, run_manual_trigger
from tests.test_api import seed_baseline


import pytest
from fastapi.testclient import TestClient
from app.store import AnnouncementStore
def test_manual_trigger_reschedules_whenplayback_lock_held(radio: StubRadio, store: AnnouncementStore, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = client
    monkeypatch.setattr("app.config.TRIGGER_NOW", True)

    def no_sleep(_seconds: float) -> None:
        return

    def chunks_for(_item: Announcement) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    def play(_chunks: Iterable[PcmChunk]) -> None:
        return

    monkeypatch.setattr("app.scheduler_fire.time.sleep", no_sleep)
    monkeypatch.setattr(
        "app.scheduler_fire.chunks_for_announcement",
        chunks_for,
    )
    monkeypatch.setattr("app.scheduler_fire.play_chunks", play)

    _announcement, schedule = seed_baseline(store)
    radio.busy = False
    defer_id = f"defer:manual:{schedule.id}"
    sched = get_scheduler()
    assert sched is not None

    assert playback_lock.acquire(blocking=False)
    try:
        result = run_manual_trigger(schedule.id)
        assert result == "deferred"
        assert sched.get_job(defer_id) is not None
    finally:
        playback_lock.release()
