"""Diagnostic tests for scheduled announcements that never play.

These pin current behavior. They do not change the app.

Production log (2026-08-27 20:00 On-the-hour):
    Run time of job "run_scheduled_fire (trigger: cron[minute='0'],
    next run at: 2026-08-27 21:00:00 EDT)" was missed by 0:02:19.548397
APScheduler then skips the run because misfire_grace_time is 30 seconds.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.fire import FireContext, handle_fire
from app.models import Announcement, Exclusion, Schedule
from app.radio import StubRadio
from app.schedule_logic import is_excluded
from app.scheduler import PromptBackgroundScheduler, get_scheduler, start_scheduler, stop_scheduler
from tests.test_fire import NOW, TZ, _announcement, _ctx, _deps


def test_apscheduler_skips_run_when_later_than_30s_grace():
    """Matches the serve log: a job more than 30s late is not executed."""
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


def test_app_hourly_jobs_are_configured_with_30s_misfire_grace(store):
    store.save(
        Announcement(
            name="Hourly",
            text="ID",
            schedules=[
                Schedule.model_validate(
                    {"kind": "hourly", "minutes": [0], "timezone": "America/New_York"}
                )
            ],
        )
    )
    start_scheduler()
    try:
        jobs = get_scheduler().get_jobs()
        hourly = [job for job in jobs if job.id and job.id.startswith("sched:")]
        assert hourly
        assert hourly[0].misfire_grace_time == 30
        assert hourly[0].coalesce is True
        assert hourly[0].max_instances == 1
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


def test_hourly_cron_after_the_minute_jumps_to_next_hour():
    """sync_jobs replace_existing uses get_next_fire_time(now). A save or restart
    a few seconds after :00 never schedules that hour."""
    tz = ZoneInfo("America/New_York")
    trigger = CronTrigger(minute="0", timezone=tz)
    after = datetime(2026, 8, 27, 20, 0, 5, tzinfo=tz)
    nxt = trigger.get_next_fire_time(None, after)
    assert nxt == datetime(2026, 8, 27, 21, 0, tzinfo=tz)


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


def test_busy_retry_can_land_inside_exclusion_and_drop():
    """handle_fire checks exclusions against deps.now, not ctx.fired_at.
    A busy deferral that slips into an exclusion window is dropped instead of playing
    the original slot."""
    radio = StubRadio()
    announcement = _announcement(
        schedule={
            "exclusions": [
                Exclusion(
                    kind="day_time",
                    days=["thu"],
                    start="21:55",
                    end="23:05",
                ).model_dump()
            ]
        }
    )
    fired_at = datetime(2026, 8, 27, 21, 50, tzinfo=TZ)
    retry_now = datetime(2026, 8, 27, 21, 56, tzinfo=TZ)
    assert not is_excluded(announcement.schedules[0], fired_at)
    assert is_excluded(announcement.schedules[0], retry_now)
    deps, extras = _deps(announcement, radio)
    deps.now = retry_now
    ctx = FireContext(
        announcement_id="ann1",
        schedule_id="sched1",
        fired_at=fired_at,
        deadline=datetime(2026, 8, 27, 22, 30, tzinfo=TZ),
    )
    assert handle_fire(ctx, deps) == "excluded"
    assert extras["played"] == []
    assert extras["deferred"] == []
    assert extras["last_runs"] == []
