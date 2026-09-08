"""Scheduler timing: misfire grace and the capped wait loop."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.fire import handle_fire
from app.models import Announcement, Schedule
from app.radio import StubRadio
from app.scheduler import PromptBackgroundScheduler, get_scheduler, start_scheduler, stop_scheduler
from tests.test_fire import _announcement, _ctx, _deps


def test_apscheduler_skips_run_when_later_than_30s_grace():
    ran: list[str] = []
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.start()
    try:
        past = datetime.now(timezone.utc) - timedelta(seconds=90)
        scheduler.add_job(
            lambda: ran.append("ran"),
            DateTrigger(run_date=past),
            misfire_grace_time=30,
        )
        deadline = time.time() + 1.0
        while time.time() < deadline and not ran:
            time.sleep(0.05)
    finally:
        scheduler.shutdown(wait=False)
    assert ran == []


def test_apscheduler_still_runs_within_30s_grace():
    ran: list[str] = []
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.start()
    try:
        past = datetime.now(timezone.utc) - timedelta(seconds=2)
        scheduler.add_job(
            lambda: ran.append("ran"),
            DateTrigger(run_date=past),
            misfire_grace_time=30,
        )
        deadline = time.time() + 1.0
        while time.time() < deadline and not ran:
            time.sleep(0.05)
    finally:
        scheduler.shutdown(wait=False)
    assert ran == ["ran"]


def test_slot_jobs_use_30s_misfire_grace(store):
    announcement = store.save_announcement(Announcement(name="Hourly", text="ID"))
    store.save_schedule(Schedule(name="Baseline", kind="baseline", announcement_id=announcement.id))
    start_scheduler()
    try:
        jobs = get_scheduler().get_jobs()
        slots = [job for job in jobs if job.id and job.id.startswith("slot:")]
        assert slots
        assert slots[0].misfire_grace_time == 30
        assert isinstance(get_scheduler(), PromptBackgroundScheduler)
        wait = get_scheduler()._process_jobs()
        assert wait is not None
        assert wait <= 5
    finally:
        stop_scheduler()


def test_prompt_scheduler_caps_wait_instead_of_sleeping_until_next_hour():
    sched = PromptBackgroundScheduler(timezone="UTC", max_wait_seconds=5)
    sched.start()
    try:
        sched.add_job(
            lambda: None,
            CronTrigger(minute="0", timezone="UTC"),
            id="hourly",
            misfire_grace_time=30,
        )
        wait = sched._process_jobs()
        assert wait is not None
        assert 0 <= wait <= 5
    finally:
        sched.shutdown(wait=False)


def test_playback_lock_contention_drops_fire_without_retry():
    radio = StubRadio()
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    extras["lock"].acquire()
    try:
        assert handle_fire(_ctx(), deps) == "skipped_lock"
    finally:
        extras["lock"].release()
    assert extras["played"] == []
    assert extras["deferred"] == []
    assert extras["last_runs"] == []
