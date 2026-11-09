"""Live status the UI shows for a manual Trigger now while it waits and transmits."""
from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import cast

from app import manual_trigger_state
from app.auth import BACKGROUND_POLL_HEADER
from app.audio import PcmChunk
from app.models import Announcement
from app.home_live import build_home_live_status
from app.fire_types import FireOutcome
from app.manual_trigger_types import ManualTriggerPhase
import pytest
from fastapi.testclient import TestClient
from app.store import AnnouncementStore
from app.radio import StubRadio
from app.manual_trigger_state import (
    clear_manual_inflight,
    pending_manual_triggers,
    pending_row_for_manual,
    set_manual_inflight,
    set_manual_inflight_deadline,
)
from app.scheduler import (
    clear_running,
    finish_manual_trigger,
    get_running,
    run_manual_trigger,
    set_running,
)
from tests.test_api import seed_baseline
from tests.jsonutil import json_dict


def _phase(*, busy: bool, idle: float, clear_seconds: float = 10.0, in_progress: bool) -> tuple[ManualTriggerPhase, float | None]:
    row = pending_row_for_manual(
        "s1",
        None,
        None,
        busy=busy,
        idle=idle,
        clear_seconds=clear_seconds,
        give_up_remaining=0.0,
        retry_in=0.0,
        in_progress=in_progress,
    )
    return row["phase"], row["clear_remaining_seconds"]


def test_inflight_retry_waiting_out_clear_hold_shows_countdown() -> None:
    assert _phase(busy=False, idle=3.0, in_progress=True) == ("clear_window", 7.0)


def test_inflight_retry_while_channel_busy_shows_busy() -> None:
    assert _phase(busy=True, idle=0.0, in_progress=True) == ("channel_busy", None)


def test_inflight_with_hold_met_is_starting() -> None:
    assert _phase(busy=False, idle=12.0, in_progress=True) == ("clear_window", 0.0)


def test_inflight_without_clear_hold_is_in_progress() -> None:
    assert _phase(busy=False, idle=0.0, clear_seconds=0.0, in_progress=True) == (
        "in_progress",
        None,
    )


def test_inflight_row_reports_give_up_remaining(store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _announcement, schedule = seed_baseline(store)

    def busy_status() -> tuple[bool, float, float]:
        return True, 0.0, 10.0

    monkeypatch.setattr(manual_trigger_state, "radio_status_for_ui", busy_status)
    set_manual_inflight(schedule.id, "x")
    try:
        from app.slots import zone_for

        zone = zone_for(store.document().settings.timezone)
        set_manual_inflight_deadline(schedule.id, datetime.now(zone) + timedelta(seconds=60))
        (row,) = pending_manual_triggers()
    finally:
        clear_manual_inflight(schedule.id)
    assert row["phase"] == "channel_busy"
    assert 55 < row["give_up_remaining_seconds"] <= 60


def test_schedules_poll_reports_running_during_immediate_manual_transmit(client: TestClient, store: AnnouncementStore, radio: StubRadio, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = radio

    def no_sleep(_seconds: float) -> None:
        return

    def chunks_for(_item: Announcement) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    monkeypatch.setattr("app.scheduler_fire.time.sleep", no_sleep)
    monkeypatch.setattr(
        "app.scheduler_fire.chunks_for_announcement",
        chunks_for,
    )
    on_air = threading.Event()
    release = threading.Event()

    def play(_chunks: Iterable[PcmChunk]) -> None:
        on_air.set()
        _ = release.wait(5)

    monkeypatch.setattr("app.scheduler_fire.play_chunks", play)
    _announcement, schedule = seed_baseline(store)
    result: dict[str, str] = {}

    def trigger() -> None:
        result["r"] = run_manual_trigger(schedule.id)

    worker = threading.Thread(target=trigger)
    worker.start()
    try:
        assert on_air.wait(5)
        payload = json_dict(client.get("/api/schedules", headers={BACKGROUND_POLL_HEADER: "1"}))
        upcoming = cast(dict[str, object], payload["upcoming"])
        assert upcoming["status"] == "running"
        assert upcoming["schedule_id"] == schedule.id
    finally:
        release.set()
        worker.join(5)
    assert result["r"] == "transmitted"
    after = json_dict(client.get("/api/schedules"))
    upcoming = cast(dict[str, object], after["upcoming"])
    assert upcoming["status"] == "waiting"
    assert after["pending_manual_triggers"] == []


def test_clear_running_while_manual_inflight_skips_intermediate_notify(store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid Running → pending flash → Waiting when Trigger now finishes."""
    announcement, schedule = seed_baseline(store)
    notifies: list[dict[str, bool]] = []

    def notify(*, force: bool = False) -> None:
        notifies.append({"force": force})

    monkeypatch.setattr(
        "app.scheduler_fire._notify_home_live",
        notify,
    )
    set_manual_inflight(schedule.id, announcement.name)
    try:
        set_running(announcement, schedule.id)
        notifies.clear()

        clear_running()
        assert get_running() is None
        assert notifies == []
        assert pending_manual_triggers()  # would flash in the UI if published

        _ = finish_manual_trigger(
            schedule.id,
            announcement,
            schedule,
            FireOutcome.TRANSMITTED,
        )
        assert notifies == []
    finally:
        clear_manual_inflight(schedule.id)

    live = build_home_live_status()
    upcoming = live["upcoming"]
    assert upcoming is not None
    assert upcoming["status"] == "waiting"
    assert live["pending_manual_triggers"] == []


def test_clear_running_without_inflight_still_notifies(store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    announcement, schedule = seed_baseline(store)
    notifies: list[dict[str, bool]] = []

    def notify(*, force: bool = False) -> None:
        notifies.append({"force": force})

    monkeypatch.setattr(
        "app.scheduler_fire._notify_home_live",
        notify,
    )
    set_running(announcement, schedule.id)
    notifies.clear()
    clear_running()
    assert notifies == [{"force": True}]
