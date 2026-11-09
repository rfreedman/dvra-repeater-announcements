"""Scheduler timing: misfire grace and the capped wait loop."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Protocol, cast

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.fire import handle_fire
from app.models import BaselineSchedule, TtsAnnouncement
from app.radio import StubRadio
from app.scheduler import PromptBackgroundScheduler, get_scheduler, start_scheduler, stop_scheduler
from tests.fire_helpers import make_announcement, make_ctx, make_deps


from app.store import AnnouncementStore


class _ScheduledJob(Protocol):
    id: str
    misfire_grace_time: int | None


def test_apscheduler_skips_run_when_later_than_30s_grace() -> None:
    ran: list[str] = []
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.start()
    try:
        past = datetime.now(timezone.utc) - timedelta(seconds=90)

        def record_run() -> None:
            ran.append("ran")

        _ = scheduler.add_job(
            record_run,
            DateTrigger(run_date=past),
            misfire_grace_time=30,
        )
        deadline = time.time() + 1.0
        while time.time() < deadline and not ran:
            time.sleep(0.05)
    finally:
        scheduler.shutdown(wait=False)
    assert ran == []


def test_apscheduler_still_runs_within_30s_grace() -> None:
    ran: list[str] = []
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.start()
    try:
        past = datetime.now(timezone.utc) - timedelta(seconds=2)

        def record_run() -> None:
            ran.append("ran")

        _ = scheduler.add_job(
            record_run,
            DateTrigger(run_date=past),
            misfire_grace_time=30,
        )
        deadline = time.time() + 1.0
        while time.time() < deadline and not ran:
            time.sleep(0.05)
    finally:
        scheduler.shutdown(wait=False)
    assert ran == ["ran"]


def test_slot_jobs_use_30s_misfire_grace(store: AnnouncementStore) -> None:
    announcement = store.save_announcement(TtsAnnouncement(name="Hourly", text="ID"))
    _ = store.save_schedule(
        BaselineSchedule(name="Baseline", announcement_id=announcement.id)
    )
    start_scheduler()
    try:
        scheduler = get_scheduler()
        assert scheduler is not None
        jobs = cast(list[_ScheduledJob], scheduler.get_jobs())
        slots = [job for job in jobs if job.id and job.id.startswith("slot:")]
        assert slots
        assert slots[0].misfire_grace_time == 30
        assert isinstance(scheduler, PromptBackgroundScheduler)
        wait = scheduler.process_due_jobs()
        assert wait is not None
        assert wait <= 5
    finally:
        stop_scheduler()


def test_prompt_scheduler_caps_wait_instead_of_sleeping_until_next_hour() -> None:
    sched = PromptBackgroundScheduler(timezone="UTC", max_wait_seconds=5)
    sched.start()
    try:
        def no_op() -> None:
            return

        _ = sched.add_job(
            no_op,
            CronTrigger(minute="0", timezone="UTC"),
            id="hourly",
            misfire_grace_time=30,
        )
        wait = sched.process_due_jobs()
        assert wait is not None
        assert 0 <= wait <= 5
    finally:
        sched.shutdown(wait=False)


def test_playback_lock_contention_drops_fire_without_retry() -> None:
    radio = StubRadio()
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    _ = extras["lock"].acquire()
    try:
        assert handle_fire(make_ctx(), deps) == "skipped_lock"
    finally:
        extras["lock"].release()
    assert extras["played"] == []
    assert extras["deferred"] == []
    assert extras["last_runs"] == []
