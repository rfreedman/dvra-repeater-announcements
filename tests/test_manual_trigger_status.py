"""Live status the UI shows for a manual Trigger now while it waits and transmits."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from app import manual_trigger_state, scheduler
from app.auth import BACKGROUND_POLL_HEADER
from app.audio import PcmChunk
from app.home_live import build_home_live_status
from app.manual_trigger_state import (
    clear_manual_inflight,
    pending_manual_triggers,
    pending_row_for_manual,
    set_manual_inflight,
    set_manual_inflight_deadline,
)
from app.scheduler import (
    _clear_running,
    _finish_manual_trigger,
    _set_running,
    get_running,
    run_manual_trigger,
)
from tests.test_api import _seed_baseline


def _phase(*, busy: bool, idle: float, clear_seconds: float = 10.0, in_progress: bool):
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


def test_inflight_retry_waiting_out_clear_hold_shows_countdown():
    assert _phase(busy=False, idle=3.0, in_progress=True) == ("clear_window", 7.0)


def test_inflight_retry_while_channel_busy_shows_busy():
    assert _phase(busy=True, idle=0.0, in_progress=True) == ("channel_busy", None)


def test_inflight_with_hold_met_is_starting():
    assert _phase(busy=False, idle=12.0, in_progress=True) == ("clear_window", 0.0)


def test_inflight_without_clear_hold_is_in_progress():
    assert _phase(busy=False, idle=0.0, clear_seconds=0.0, in_progress=True) == (
        "in_progress",
        None,
    )


def test_inflight_row_reports_give_up_remaining(store, monkeypatch):
    _announcement, schedule = _seed_baseline(store)
    monkeypatch.setattr(manual_trigger_state, "radio_status_for_ui", lambda: (True, 0.0, 10.0))
    set_manual_inflight(schedule.id, "x")
    try:
        zone = scheduler.zone_for(store.document().settings.timezone)
        set_manual_inflight_deadline(schedule.id, datetime.now(zone) + timedelta(seconds=60))
        (row,) = pending_manual_triggers()
    finally:
        clear_manual_inflight(schedule.id)
    assert row["phase"] == "channel_busy"
    assert 55 < row["give_up_remaining_seconds"] <= 60


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
        payload = client.get("/api/schedules", headers={BACKGROUND_POLL_HEADER: "1"}).json()
        assert payload["upcoming"]["status"] == "running"
        assert payload["upcoming"]["schedule_id"] == schedule.id
    finally:
        release.set()
        worker.join(5)
    assert result["r"] == "transmitted"
    after = client.get("/api/schedules").json()
    assert after["upcoming"]["status"] == "waiting"
    assert after["pending_manual_triggers"] == []


def test_clear_running_while_manual_inflight_skips_intermediate_notify(store, monkeypatch):
    """Avoid Running → pending flash → Waiting when Trigger now finishes."""
    announcement, schedule = _seed_baseline(store)
    notifies: list[dict] = []
    monkeypatch.setattr(
        "app.scheduler._notify_home_live",
        lambda *, force=False: notifies.append({"force": force}),
    )
    set_manual_inflight(schedule.id, announcement.name)
    try:
        _set_running(announcement, schedule.id)
        notifies.clear()

        _clear_running()
        assert get_running() is None
        assert notifies == []
        assert pending_manual_triggers()  # would flash in the UI if published

        _finish_manual_trigger(schedule.id, announcement, schedule, "transmitted")
        assert notifies == []
    finally:
        clear_manual_inflight(schedule.id)

    live = build_home_live_status()
    assert live["upcoming"]["status"] == "waiting"
    assert live["pending_manual_triggers"] == []


def test_clear_running_without_inflight_still_notifies(store, monkeypatch):
    announcement, schedule = _seed_baseline(store)
    notifies: list[dict] = []
    monkeypatch.setattr(
        "app.scheduler._notify_home_live",
        lambda *, force=False: notifies.append({"force": force}),
    )
    _set_running(announcement, schedule.id)
    notifies.clear()
    _clear_running()
    assert notifies == [{"force": True}]
