"""Manual trigger busy defer must schedule an APScheduler job that later transmits."""
from __future__ import annotations

import time

import pytest

from app.audio import PcmChunk
from app.scheduler import get_scheduler
from tests.test_api import _seed_baseline


@pytest.mark.parametrize("busy_retry_seconds", [0.2])
def test_manual_busy_defers_then_transmits_via_scheduler(
    radio, store, client, monkeypatch, busy_retry_seconds
):
    monkeypatch.setattr("app.config.TRIGGER_NOW", True)
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "app.scheduler.chunks_for_announcement",
        lambda _item: [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
    )
    monkeypatch.setattr("app.scheduler.play_chunks", lambda _chunks: None)

    announcement, schedule = _seed_baseline(store)
    store.save_announcement(
        announcement.model_copy(
            update={
                "busy_retry_seconds": busy_retry_seconds,
                "busy_give_up_seconds": 30,
            }
        )
    )

    # First fire: busy at start. Retry job: channel clear through transmit.
    radio.busy_sequence = [True, False, False, False, False]
    radio._busy_index = 0

    sched = get_scheduler()
    assert sched is not None
    defer_id = f"defer:manual:{schedule.id}"

    res = client.post(f"/api/schedules/{schedule.id}/trigger")
    assert res.status_code == 200
    assert res.json()["result"] == "deferred"
    home = client.get("/api/schedules").json()
    pending = home["pending_manual_triggers"]
    assert len(pending) == 1
    assert pending[0]["schedule_id"] == schedule.id
    assert pending[0]["announcement_name"] == announcement.name
    assert pending[0]["phase"] in {"channel_busy", "retry_pending", "clear_window"}
    jobs = {job.id for job in sched.get_jobs()}
    assert defer_id in jobs, f"expected defer job; jobs={jobs}"

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ("ptt", True) in radio.events:
            break
        time.sleep(0.05)
    assert ("ptt", True) in radio.events, f"defer retry never transmitted; events={radio.events}"

    assert defer_id not in {job.id for job in sched.get_jobs()}
