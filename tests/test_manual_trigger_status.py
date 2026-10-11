"""While Trigger now transmits inside the HTTP request, polls must already report it running."""
from __future__ import annotations

import threading

from app.audio import PcmChunk
from app.scheduler import run_manual_trigger
from tests.test_api import _seed_baseline


def test_schedules_poll_reports_running_during_immediate_manual_transmit(
    client, store, radio, monkeypatch
):
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "app.scheduler.chunks_for_announcement",
        lambda _item: [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
    )
    on_air = threading.Event()
    release = threading.Event()

    def play(_chunks):
        on_air.set()
        release.wait(5)

    monkeypatch.setattr("app.scheduler.play_chunks", play)
    _announcement, schedule = _seed_baseline(store)
    result: dict[str, str] = {}
    worker = threading.Thread(target=lambda: result.update(r=run_manual_trigger(schedule.id)))
    worker.start()
    try:
        assert on_air.wait(5)
        payload = client.get("/api/schedules", headers={"X-Background-Poll": "1"}).json()
        assert payload["upcoming"]["status"] == "running"
        assert payload["upcoming"]["schedule_id"] == schedule.id
    finally:
        release.set()
        worker.join(5)
    assert result["r"] == "transmitted"
    after = client.get("/api/schedules").json()
    assert after["upcoming"]["status"] == "waiting"
    assert after["pending_manual_triggers"] == []
