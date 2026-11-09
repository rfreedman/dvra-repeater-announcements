"""Manual trigger busy defer must schedule an APScheduler job that later transmits."""
from __future__ import annotations
from collections.abc import Iterable
from app.radio import StubRadio

import time
from typing import Protocol, cast

import pytest

from app.audio import PcmChunk
from app.models import Announcement
from app.scheduler import get_scheduler
from tests.test_api import seed_baseline


from fastapi.testclient import TestClient
from app.store import AnnouncementStore
from tests.jsonutil import json_dict


class _Job(Protocol):
    id: str


@pytest.mark.parametrize("busy_retry_seconds", [0.2])
def test_manual_busy_defers_then_transmits_via_scheduler(radio: StubRadio, store: AnnouncementStore, client: TestClient, monkeypatch: pytest.MonkeyPatch, busy_retry_seconds: float) -> None:
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

    announcement, schedule = seed_baseline(store)
    _ = store.save_announcement(
        announcement.model_copy(
            update={
                "busy_retry_seconds": busy_retry_seconds,
                "busy_give_up_seconds": 30,
            }
        )
    )

    # First fire: busy at start. Retry job: channel clear through transmit.
    radio.busy_sequence = [True, False, False, False, False]
    radio.restart_busy_sequence()

    sched = get_scheduler()
    assert sched is not None
    defer_id = f"defer:manual:{schedule.id}"

    res = client.post(f"/api/schedules/{schedule.id}/trigger")
    assert res.status_code == 200
    assert json_dict(res)["result"] == "deferred"
    home = json_dict(client.get("/api/schedules"))
    pending = cast(list[object], home["pending_manual_triggers"])
    assert len(pending) == 1
    row = cast(dict[str, object], pending[0])
    assert row["schedule_id"] == schedule.id
    assert row["announcement_name"] == announcement.name
    assert row["phase"] in {"channel_busy", "retry_pending", "clear_window"}
    jobs = {job.id for job in cast(list[_Job], sched.get_jobs())}
    assert defer_id in jobs, f"expected defer job; jobs={jobs}"

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ("ptt", True) in radio.events:
            break
        time.sleep(0.05)
    assert ("ptt", True) in radio.events, f"defer retry never transmitted; events={radio.events}"

    assert defer_id not in {job.id for job in cast(list[_Job], sched.get_jobs())}
