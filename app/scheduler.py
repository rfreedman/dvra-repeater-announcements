from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import POINTER, byref, c_char_p, c_int32, c_uint32, c_void_p
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.config import PTT_LEAD_SECONDS, SCHEDULER_MAX_WAIT_SECONDS, TIMEZONE
from app.fire import FireContext, FireDeps, handle_fire
from app.playback import play_chunks
from app.radio import get_radio
from app.registry import get_registry
from app.models import parse_hhmm
from app.store import get_store

log = logging.getLogger("app.scheduler")

_scheduler: BackgroundScheduler | None = None
_playback_lock = threading.Lock()
_idle_assertion: c_uint32 | None = None


class PromptBackgroundScheduler(BackgroundScheduler):
    """Wake often enough that a long sleep cannot miss a cron by minutes.

    APScheduler otherwise waits until the next job. On macOS that wait can be
    App-Napped for well over the 30s misfire window.
    """

    def __init__(self, *args, max_wait_seconds: float = SCHEDULER_MAX_WAIT_SECONDS, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_wait_seconds = max(0.5, float(max_wait_seconds))

    def _process_jobs(self):
        wait = super()._process_jobs()
        if wait is None:
            return self.max_wait_seconds
        return min(float(wait), self.max_wait_seconds)


def _inhibit_idle_sleep() -> None:
    """Ask macOS not to idle-sleep / App Nap this process (no-op elsewhere)."""
    global _idle_assertion
    if sys.platform != "darwin" or _idle_assertion is not None:
        return
    try:
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        k_utf8 = 0x08000100
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        cf.CFStringCreateWithCString.restype = c_void_p
        iokit.IOPMAssertionCreateWithName.argtypes = [
            c_void_p,
            c_uint32,
            c_void_p,
            POINTER(c_uint32),
        ]
        iokit.IOPMAssertionCreateWithName.restype = c_int32
        assertion_id = c_uint32(0)
        status = iokit.IOPMAssertionCreateWithName(
            cf.CFStringCreateWithCString(None, b"PreventUserIdleSystemSleep", k_utf8),
            255,
            cf.CFStringCreateWithCString(None, b"W2ZQ announcement scheduler", k_utf8),
            byref(assertion_id),
        )
        if status != 0:
            log.warning("Idle-sleep assertion failed: %s", status)
            return
        _idle_assertion = assertion_id
        log.info("Inhibited idle sleep so schedule wakes stay on time")
    except Exception:
        log.warning("Could not inhibit idle sleep", exc_info=True)


def _allow_idle_sleep() -> None:
    global _idle_assertion
    if _idle_assertion is None:
        return
    try:
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOPMAssertionRelease.argtypes = [c_uint32]
        iokit.IOPMAssertionRelease.restype = c_int32
        iokit.IOPMAssertionRelease(_idle_assertion)
    except Exception:
        log.debug("Could not release idle-sleep assertion", exc_info=True)
    _idle_assertion = None


def _job_id(schedule_id: str) -> str:
    return f"sched:{schedule_id}"


def _defer_id(schedule_id: str) -> str:
    return f"defer:{schedule_id}"


def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name or TIMEZONE)


def run_scheduled_fire(
    announcement_id: str,
    schedule_id: str,
    fired_at_iso: str | None = None,
    deadline_iso: str | None = None,
) -> str:
    store = get_store()
    announcement = store.get(announcement_id)
    if announcement is None:
        return "missing"
    schedule = announcement.schedule_by_id(schedule_id)
    if schedule is None:
        return "missing"
    zone = _zone(schedule.timezone)
    now = datetime.now(zone)
    if fired_at_iso:
        fired_at = datetime.fromisoformat(fired_at_iso)
        if fired_at.tzinfo is None:
            fired_at = fired_at.replace(tzinfo=zone)
    else:
        fired_at = now
    if deadline_iso:
        deadline = datetime.fromisoformat(deadline_iso)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=zone)
    else:
        deadline = fired_at + timedelta(seconds=announcement.busy_give_up_seconds)
    ctx = FireContext(
        announcement_id=announcement_id,
        schedule_id=schedule_id,
        fired_at=fired_at,
        deadline=deadline,
    )

    def load() -> tuple:
        current = store.get(announcement_id)
        if current is None:
            return None
        row = current.schedule_by_id(schedule_id)
        if row is None:
            return None
        return current, row

    def synthesize(item) -> object:
        _voice, _rate, chunks = get_registry().synthesize(
            item.text,
            voice=item.voice,
            speed=item.speed,
            sentence_pause=item.sentence_pause,
        )
        return chunks

    def defer(run_at: datetime, fire_ctx: FireContext) -> None:
        sched = get_scheduler()
        if sched is None:
            return
        sched.add_job(
            run_scheduled_fire,
            DateTrigger(run_date=run_at),
            id=_defer_id(schedule_id),
            replace_existing=True,
            kwargs={
                "announcement_id": fire_ctx.announcement_id,
                "schedule_id": fire_ctx.schedule_id,
                "fired_at_iso": fire_ctx.fired_at.isoformat(),
                "deadline_iso": fire_ctx.deadline.isoformat(),
            },
        )

    deps = FireDeps(
        now=now,
        radio=get_radio(),
        play_fn=play_chunks,
        sleep_fn=time.sleep,
        schedule_defer=defer,
        playback_lock=_playback_lock,
        synthesize=synthesize,
        mark_last_run=store.set_last_run,
        load=load,
        ptt_lead_seconds=PTT_LEAD_SECONDS,
    )
    return handle_fire(ctx, deps)


def _add_schedule_jobs(scheduler: BackgroundScheduler, announcement_id: str, schedule) -> None:
    job_kwargs = {
        "announcement_id": announcement_id,
        "schedule_id": schedule.id,
    }
    tz = _zone(schedule.timezone)
    if schedule.kind == "hourly":
        minutes = schedule.minutes or ([schedule.minute] if schedule.minute is not None else [0])
        scheduler.add_job(
            run_scheduled_fire,
            CronTrigger(minute=",".join(str(item) for item in minutes), timezone=tz),
            id=_job_id(schedule.id),
            replace_existing=True,
            kwargs=job_kwargs,
            misfire_grace_time=30,
        )
        return
    if schedule.kind in {"daily", "weekly"}:
        days = ",".join(schedule.days) if schedule.kind == "weekly" else None
        for stamp in schedule.times:
            hour, minute = parse_hhmm(stamp)
            trigger_kwargs = {"hour": hour, "minute": minute, "timezone": tz}
            if days:
                trigger_kwargs["day_of_week"] = days
            scheduler.add_job(
                run_scheduled_fire,
                CronTrigger(**trigger_kwargs),
                id=f"{_job_id(schedule.id)}:{stamp.replace(':', '')}",
                replace_existing=True,
                kwargs=job_kwargs,
                misfire_grace_time=30,
            )
        return
    if schedule.at is not None:
        when = schedule.at if schedule.at.tzinfo else schedule.at.replace(tzinfo=tz)
        if when > datetime.now(tz):
            scheduler.add_job(
                run_scheduled_fire,
                DateTrigger(run_date=when),
                id=_job_id(schedule.id),
                replace_existing=True,
                kwargs=job_kwargs,
                misfire_grace_time=30,
            )


def sync_jobs() -> None:
    scheduler = get_scheduler()
    if scheduler is None:
        return
    wanted: set[str] = set()
    for announcement in get_store().list_announcements():
        for schedule in announcement.schedules:
            if not schedule.enabled:
                continue
            if schedule.kind in {"daily", "weekly"}:
                for stamp in schedule.times:
                    wanted.add(f"{_job_id(schedule.id)}:{stamp.replace(':', '')}")
            else:
                wanted.add(_job_id(schedule.id))
            _add_schedule_jobs(scheduler, announcement.id, schedule)
    for job in list(scheduler.get_jobs()):
        jid = job.id or ""
        if jid.startswith("sched:") and jid not in wanted:
            scheduler.remove_job(jid)
        if jid.startswith("defer:"):
            sid = jid.split(":", 1)[1]
            if f"sched:{sid}" not in wanted and not any(item.startswith(f"sched:{sid}:") for item in wanted):
                scheduler.remove_job(jid)


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        sync_jobs()
        return
    scheduler = PromptBackgroundScheduler(timezone=TIMEZONE, max_wait_seconds=SCHEDULER_MAX_WAIT_SECONDS)
    scheduler.start()
    _scheduler = scheduler
    _inhibit_idle_sleep()
    sync_jobs()
    log.info("Scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    _allow_idle_sleep()
    log.info("Scheduler stopped")
