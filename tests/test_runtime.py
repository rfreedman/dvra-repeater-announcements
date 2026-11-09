"""Process services share one runtime; SSE publishers are wired explicitly."""

from __future__ import annotations

from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from app.live_notify import notify_home_data_changed, notify_home_live_status
from app.radio import StubRadio, get_radio, set_radio
from app.runtime import get_runtime
from app.scheduler import run_scheduled_fire, wire_scheduler_runners
from app.scheduler_instance import get_scheduler, get_scheduled_fire_runner, set_scheduler
from app.sse_hub import publish_home_live_status, wire_home_live_publishers
from app.store import AnnouncementStore, get_store, set_store


def test_setters_write_the_process_runtime(tmp_path: Path) -> None:
    store = AnnouncementStore(tmp_path / "announcements.json")
    radio = StubRadio()
    previous_store = get_runtime().store
    previous_radio = get_runtime().radio
    set_store(store)
    set_radio(radio)
    try:
        runtime = get_runtime()
        assert runtime.store is store
        assert runtime.radio is radio
        assert get_store() is store
        assert get_radio() is radio
    finally:
        get_runtime().store = previous_store
        get_runtime().radio = previous_radio


def test_scheduler_slot_is_on_the_runtime() -> None:
    sched = BackgroundScheduler(timezone="UTC")
    previous = get_scheduler()
    set_scheduler(sched)
    try:
        assert get_runtime().scheduler is sched
        assert get_scheduler() is sched
    finally:
        set_scheduler(previous)


def test_fire_runner_is_published_on_the_runtime() -> None:
    previous = get_runtime().scheduled_fire_runner
    get_runtime().scheduled_fire_runner = None
    try:
        wire_scheduler_runners()
        assert get_scheduled_fire_runner() is run_scheduled_fire
        assert get_runtime().scheduled_fire_runner is run_scheduled_fire
    finally:
        get_runtime().scheduled_fire_runner = previous


def test_notify_is_a_noop_until_sse_publishers_are_wired() -> None:
    runtime = get_runtime()
    previous_status = runtime.status_publisher
    previous_data = runtime.data_changed_publisher
    previous_throttled = runtime.throttled_publisher
    runtime.status_publisher = None
    runtime.data_changed_publisher = None
    runtime.throttled_publisher = None
    try:
        notify_home_live_status(force=True)
        notify_home_data_changed()
        assert runtime.status_publisher is None
        wire_home_live_publishers()
        assert runtime.status_publisher is publish_home_live_status
    finally:
        runtime.status_publisher = previous_status
        runtime.data_changed_publisher = previous_data
        runtime.throttled_publisher = previous_throttled
