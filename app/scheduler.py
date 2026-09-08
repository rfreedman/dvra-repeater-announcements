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
from apscheduler.triggers.date import DateTrigger

from app.config import LOOKAHEAD_HOURS, PTT_LEAD_SECONDS, SCHEDULER_MAX_WAIT_SECONDS, TIMEZONE
from app.fire import FireContext, FireDeps, LoadedFire, handle_fire
from app.playback import play_chunks
from app.radio import get_radio
from app.registry import get_registry
from app.schedule_logic import resolve_slot, resolve_window
from app.slots import slot_from_key, zone_for
from app.store import get_store

log = logging.getLogger("app.scheduler")

_scheduler: BackgroundScheduler | None = None
_playback_lock = threading.Lock()
_idle_assertion: c_uint32 | None = None
_running_lock = threading.Lock()
_running: dict[str, object] | None = None


class PromptBackgroundScheduler(BackgroundScheduler):
    """Wake often enough that a long sleep cannot miss a fire by minutes.

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


def get_running() -> dict[str, object] | None:
    with _running_lock:
        return dict(_running) if _running else None


def _set_running(announcement, schedule_id: str) -> None:
    global _running
    with _running_lock:
        _running = {
            "announcement_id": announcement.id,
            "announcement_name": announcement.name,
            "schedule_id": schedule_id,
            "started_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat(),
        }


def _clear_running() -> None:
    global _running
    with _running_lock:
        _running = None


def _slot_job_id(slot_key: str) -> str:
    return f"slot:{slot_key}"


def _defer_id(slot_key: str) -> str:
    return f"defer:{slot_key}"


def run_scheduled_fire(
    slot_key: str,
    fired_at_iso: str | None = None,
    deadline_iso: str | None = None,
) -> str:
    store = get_store()
    document = store.document()
    zone = zone_for(document.settings.timezone)
    slot = slot_from_key(slot_key, zone)
    resolved = resolve_slot(document, slot)
    now = datetime.now(zone)
    if fired_at_iso:
        fired_at = datetime.fromisoformat(fired_at_iso)
        if fired_at.tzinfo is None:
            fired_at = fired_at.replace(tzinfo=zone)
    else:
        fired_at = resolved.fire_at
    announcement = resolved.announcement
    give_up = announcement.busy_give_up_seconds if announcement else 45
    if deadline_iso:
        deadline = datetime.fromisoformat(deadline_iso)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=zone)
    else:
        deadline = fired_at + timedelta(seconds=give_up)
    ctx = FireContext(
        announcement_id=announcement.id if announcement else None,
        schedule_id=resolved.schedule.id if resolved.schedule else None,
        slot_key=slot.key,
        fired_at=fired_at,
        deadline=deadline,
    )

    def load() -> LoadedFire | None:
        current = store.document()
        again = resolve_slot(current, slot)
        if again.schedule is None:
            return None
        return LoadedFire(
            announcement=again.announcement,
            schedule=again.schedule,
            silence=again.source == "silence",
        )

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
            id=_defer_id(slot_key),
            replace_existing=True,
            kwargs={
                "slot_key": fire_ctx.slot_key,
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
        consume_baseline=store.consume_baseline_slot,
        load=load,
        ptt_lead_seconds=PTT_LEAD_SECONDS,
        set_running=_set_running,
        clear_running=_clear_running,
    )
    result = handle_fire(ctx, deps)
    sync_jobs()
    return result


def sync_jobs() -> None:
    scheduler = get_scheduler()
    if scheduler is None:
        return
    document = get_store().document()
    zone = zone_for(document.settings.timezone)
    now = datetime.now(zone)
    until = now + timedelta(hours=LOOKAHEAD_HOURS)
    wanted: set[str] = set()
    for item in resolve_window(document, now - timedelta(seconds=30), until):
        if item.source == "none":
            continue
        if item.fire_at < now - timedelta(seconds=30):
            continue
        jid = _slot_job_id(item.slot.key)
        wanted.add(jid)
        scheduler.add_job(
            run_scheduled_fire,
            DateTrigger(run_date=item.fire_at),
            id=jid,
            replace_existing=True,
            kwargs={"slot_key": item.slot.key},
            misfire_grace_time=30,
        )
    for job in list(scheduler.get_jobs()):
        jid = job.id or ""
        if jid.startswith("slot:") and jid not in wanted:
            scheduler.remove_job(jid)
        if jid.startswith("defer:"):
            key = jid.split(":", 1)[1]
            if _slot_job_id(key) not in wanted:
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
    _clear_running()
    _allow_idle_sleep()
    log.info("Scheduler stopped")
