from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import POINTER, byref, c_char_p, c_int32, c_uint32, c_void_p
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from app.config import (
    DRA_CLEAR_SECONDS,
    LOOKAHEAD_HOURS,
    PTT_LEAD_SECONDS,
    SCHEDULER_MAX_WAIT_SECONDS,
    TIMEZONE,
)
from app.cos_track import start_cos_tracking, stop_cos_tracking
from app.fire import (
    FireContext,
    FireDeps,
    LoadedFire,
    defer_retry_delay,
    handle_fire,
)
from app.fire_trace import fire_trace
from app.pcm_cache import chunks_for_announcement
from app.playback import play_chunks
from app.radio import get_radio
from app.schedule_logic import resolve_slot, resolve_window
from app.slots import slot_from_key, zone_for
from app.store import get_store

log = logging.getLogger("app.scheduler")

_scheduler: BackgroundScheduler | None = None
_playback_lock = threading.Lock()
_idle_assertion: c_uint32 | None = None
_running_lock = threading.Lock()
_running: dict[str, object] | None = None
_manual_drop_lock = threading.Lock()
_manual_drop_notice: dict[str, object] | None = None
_manual_inflight_lock = threading.Lock()
_manual_inflight: dict[str, dict[str, object]] = {}

MANUAL_DROP_REASONS: dict[str, str] = {
    "give_up": "The channel did not stay clear long enough before the give-up timer expired.",
    "give_up_lock": "Another announcement was still playing when the give-up timer expired.",
    "scheduler_error": "The retry could not be scheduled (scheduler error).",
}


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


def peek_manual_trigger_drop_notice() -> dict[str, object] | None:
    with _manual_drop_lock:
        return dict(_manual_drop_notice) if _manual_drop_notice else None


def clear_manual_trigger_drop_notice() -> None:
    global _manual_drop_notice
    with _manual_drop_lock:
        _manual_drop_notice = None


def record_manual_trigger_drop(
    schedule_id: str,
    *,
    announcement_name: str,
    reason: str,
) -> None:
    global _manual_drop_notice
    detail = MANUAL_DROP_REASONS.get(reason, MANUAL_DROP_REASONS["give_up"])
    with _manual_drop_lock:
        _manual_drop_notice = {
            "schedule_id": schedule_id,
            "announcement_name": announcement_name,
            "reason": reason,
            "detail": detail,
            "at": datetime.now(ZoneInfo(TIMEZONE)).isoformat(),
        }
    log.info(
        "Manual trigger give-up; schedule=%s announcement=%s reason=%s",
        schedule_id,
        announcement_name,
        reason,
    )


def _manual_announcement_name(announcement, schedule) -> str:
    if announcement is not None and announcement.name:
        return announcement.name
    if schedule is not None and schedule.name:
        return schedule.name
    return "Untitled"


def _finish_manual_trigger(
    schedule_id: str,
    announcement,
    schedule,
    result: str,
    *,
    drop_reason: str | None = None,
) -> str:
    if result == "transmitted":
        clear_manual_trigger_drop_notice()
    elif result == "dropped":
        record_manual_trigger_drop(
            schedule_id,
            announcement_name=_manual_announcement_name(announcement, schedule),
            reason=drop_reason or "give_up",
        )
    fire_trace(
        "Manual trigger finished schedule=%s result=%s drop_reason=%s",
        schedule_id,
        result,
        drop_reason,
    )
    return result


def _slot_job_id(slot_key: str) -> str:
    return f"slot:{slot_key}"


def _defer_id(slot_key: str) -> str:
    return f"defer:{slot_key}"


def _slot_key_from_defer_job_id(jid: str) -> str | None:
    if not jid.startswith("defer:"):
        return None
    return jid[len("defer:") :]


def _normalize_job_run_at(run_at: datetime) -> datetime:
    zone = ZoneInfo(TIMEZONE)
    if run_at.tzinfo is None:
        return run_at.replace(tzinfo=zone)
    return run_at.astimezone(zone)


def _defer_misfire_grace_seconds() -> int:
    return 120


def _set_manual_inflight(schedule_id: str, announcement_name: str) -> None:
    with _manual_inflight_lock:
        _manual_inflight[schedule_id] = {
            "schedule_id": schedule_id,
            "announcement_name": announcement_name,
            "started_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat(),
        }


def _clear_manual_inflight(schedule_id: str) -> None:
    with _manual_inflight_lock:
        _manual_inflight.pop(schedule_id, None)


def any_manual_trigger_inflight() -> bool:
    with _manual_inflight_lock:
        return bool(_manual_inflight)


def _pending_row_for_manual(
    schedule_id: str,
    announcement,
    schedule,
    *,
    zone: ZoneInfo,
    now: datetime,
    busy: bool,
    idle: float,
    clear_seconds: float,
    give_up_remaining: float,
    retry_in: float | None,
    in_progress: bool,
) -> dict[str, object]:
    if clear_seconds > 0 and idle >= clear_seconds:
        phase = "clear_window"
        clear_remaining = 0.0
    elif in_progress:
        phase = "in_progress"
        clear_remaining = (
            max(0.0, clear_seconds - idle) if clear_seconds > 0 else None
        )
    elif busy:
        phase = "channel_busy"
        clear_remaining = None
    elif clear_seconds > 0:
        phase = "clear_window"
        clear_remaining = max(0.0, clear_seconds - idle)
    else:
        phase = "retry_pending"
        clear_remaining = None
    return {
        "schedule_id": schedule_id,
        "announcement_id": announcement.id if announcement else None,
        "announcement_name": _manual_announcement_name(announcement, schedule),
        "phase": phase,
        "clear_seconds": clear_seconds,
        "clear_remaining_seconds": clear_remaining,
        "give_up_remaining_seconds": give_up_remaining,
        "retry_in_seconds": retry_in,
        "in_progress": in_progress,
    }


def nudge_manual_defer_jobs_if_clear_ready() -> None:
    """Run queued manual retries as soon as the clear hold is satisfied (not on retry timer)."""
    if DRA_CLEAR_SECONDS <= 0:
        return
    sched = get_scheduler()
    if sched is None:
        return
    radio = get_radio()
    idle_fn = getattr(radio, "continuous_idle_seconds", None)
    if idle_fn is None:
        return
    idle = float(idle_fn())
    if idle < DRA_CLEAR_SECONDS:
        return
    zone = ZoneInfo(TIMEZONE)
    now = datetime.now(zone)
    for job in list(sched.get_jobs()):
        key = _slot_key_from_defer_job_id(job.id or "")
        if key is None or not key.startswith("manual:"):
            continue
        next_run = job.next_run_time
        if next_run is None:
            continue
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=zone)
        wait_s = (next_run.astimezone(zone) - now).total_seconds()
        if wait_s <= 0.2:
            continue
        kwargs = dict(job.kwargs or {})
        defer_kind = kwargs.get("defer_kind")
        # Short PTT retries (~1s) should run on their timer, not get yanked to 50ms.
        if defer_kind == "ptt" and wait_s <= 2.0:
            continue
        # idle >= clear hold: pull long backoffs onto the scheduler now.
        if wait_s > 2.0 and defer_kind not in ("busy", "clear", "ptt"):
            continue
        if wait_s > 2.0 and defer_kind is None:
            continue
        jid = job.id or _defer_id(key)
        sched.add_job(
            run_manual_trigger,
            DateTrigger(run_date=_normalize_job_run_at(now + timedelta(milliseconds=50))),
            id=jid,
            replace_existing=True,
            kwargs=kwargs,
            misfire_grace_time=_defer_misfire_grace_seconds(),
        )
        fire_trace(
            "Nudged manual defer job %s (idle=%.1fs, was %.1fs until run)",
            jid,
            idle,
            wait_s,
        )
        return


def pending_manual_triggers() -> list[dict[str, object]]:
    """Live status for manual Trigger now retries (busy defer / clear-window wait)."""
    nudge_manual_defer_jobs_if_clear_ready()
    sched = get_scheduler()
    store = get_store()
    document = store.document()
    zone = zone_for(document.settings.timezone)
    now = datetime.now(zone)
    radio = get_radio()
    wait_clear = getattr(radio, "wait_for_transmit_clear", None)
    clear_seconds = float(DRA_CLEAR_SECONDS) if wait_clear is not None and DRA_CLEAR_SECONDS > 0 else 0.0
    snapshot = getattr(radio, "cos_defer_snapshot", None)
    if snapshot is not None:
        last_busy, idle = snapshot()
        busy = True if last_busy is None else bool(last_busy)
    else:
        idle_fn = getattr(radio, "continuous_idle_seconds", None)
        busy = bool(radio.channel_busy())
        idle = float(idle_fn()) if idle_fn is not None else 0.0

    out: list[dict[str, object]] = []
    seen: set[str] = set()
    if sched is not None:
        for job in sched.get_jobs():
            key = _slot_key_from_defer_job_id(job.id or "")
            if key is None or not key.startswith("manual:"):
                continue
            schedule_id = key[len("manual:") :]
            schedule = store.get_schedule(schedule_id)
            if schedule is None:
                continue
            announcement = (
                store.get(schedule.announcement_id) if schedule.announcement_id else None
            )
            kwargs = job.kwargs or {}
            deadline_iso = kwargs.get("deadline_iso")
            give_up_remaining = 0.0
            if deadline_iso:
                deadline = datetime.fromisoformat(str(deadline_iso))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=zone)
                give_up_remaining = max(0.0, (deadline - now).total_seconds())

            retry_in: float | None = None
            next_run = job.next_run_time
            if next_run is not None:
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=zone)
                retry_in = max(0.0, (next_run.astimezone(zone) - now).total_seconds())

            seen.add(schedule_id)
            out.append(
                _pending_row_for_manual(
                    schedule_id,
                    announcement,
                    schedule,
                    zone=zone,
                    now=now,
                    busy=busy,
                    idle=idle,
                    clear_seconds=clear_seconds,
                    give_up_remaining=give_up_remaining,
                    retry_in=retry_in,
                    in_progress=False,
                )
            )

    with _manual_inflight_lock:
        inflight = dict(_manual_inflight)
    for schedule_id, _meta in inflight.items():
        if schedule_id in seen:
            schedule = store.get_schedule(schedule_id)
            if schedule is not None:
                announcement = (
                    store.get(schedule.announcement_id)
                    if schedule.announcement_id
                    else None
                )
                for row in out:
                    if row.get("schedule_id") != schedule_id:
                        continue
                    give_up = float(row.get("give_up_remaining_seconds") or 0)
                    row.update(
                        _pending_row_for_manual(
                            schedule_id,
                            announcement,
                            schedule,
                            zone=zone,
                            now=now,
                            busy=busy,
                            idle=idle,
                            clear_seconds=clear_seconds,
                            give_up_remaining=give_up,
                            retry_in=0.0,
                            in_progress=True,
                        )
                    )
                    break
            continue
        schedule = store.get_schedule(schedule_id)
        if schedule is None:
            continue
        announcement = (
            store.get(schedule.announcement_id) if schedule.announcement_id else None
        )
        give_up_remaining = 0.0
        out.append(
            _pending_row_for_manual(
                schedule_id,
                announcement,
                schedule,
                zone=zone,
                now=now,
                busy=busy,
                idle=idle,
                clear_seconds=clear_seconds,
                give_up_remaining=give_up_remaining,
                retry_in=0.0,
                in_progress=True,
            )
        )
    return out


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
        return chunks_for_announcement(item)

    def defer(run_at: datetime, fire_ctx: FireContext) -> None:
        sched = get_scheduler()
        if sched is None:
            log.error("Cannot defer fire for slot %s: scheduler not running", slot_key)
            return
        sched.add_job(
            run_scheduled_fire,
            DateTrigger(run_date=_normalize_job_run_at(run_at)),
            id=_defer_id(slot_key),
            replace_existing=True,
            kwargs={
                "slot_key": fire_ctx.slot_key,
                "fired_at_iso": fire_ctx.fired_at.isoformat(),
                "deadline_iso": fire_ctx.deadline.isoformat(),
            },
            misfire_grace_time=_defer_misfire_grace_seconds(),
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


def run_manual_trigger(
    schedule_id: str,
    *,
    fired_at_iso: str | None = None,
    deadline_iso: str | None = None,
    defer_kind: str | None = None,
) -> str:
    """Trigger now — same ``handle_fire`` / busy defer / clear wait as scheduled slots."""
    _ = defer_kind  # APScheduler job kwarg (nudge logic); overwritten on each defer
    store = get_store()
    schedule = store.get_schedule(schedule_id)
    if schedule is None:
        return "missing"
    announcement = store.get(schedule.announcement_id) if schedule.announcement_id else None
    _set_manual_inflight(schedule_id, _manual_announcement_name(announcement, schedule))
    try:
        return _run_manual_trigger_body(
            schedule_id,
            schedule,
            announcement,
            fired_at_iso=fired_at_iso,
            deadline_iso=deadline_iso,
        )
    finally:
        _clear_manual_inflight(schedule_id)


def _run_manual_trigger_body(
    schedule_id: str,
    schedule,
    announcement,
    *,
    fired_at_iso: str | None,
    deadline_iso: str | None,
) -> str:
    store = get_store()
    document = store.document()
    zone = zone_for(document.settings.timezone)
    now = datetime.now(zone)
    give_up = announcement.busy_give_up_seconds if announcement else 45
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
        deadline = fired_at + timedelta(seconds=give_up)
    slot_key = f"manual:{schedule_id}"
    ctx = FireContext(
        announcement_id=announcement.id if announcement else None,
        schedule_id=schedule.id,
        slot_key=slot_key,
        fired_at=fired_at,
        deadline=deadline,
    )

    def load() -> LoadedFire | None:
        current = store.get_schedule(schedule_id)
        if current is None:
            return None
        item = store.get(current.announcement_id) if current.announcement_id else None
        return LoadedFire(
            announcement=item,
            schedule=current,
            silence=current.is_silence,
        )

    def defer(run_at: datetime, fire_ctx: FireContext) -> None:
        sched = get_scheduler()
        if sched is None:
            log.error(
                "Cannot defer manual trigger for schedule %s: scheduler not running",
                schedule_id,
            )
            return
        sched.add_job(
            run_manual_trigger,
            DateTrigger(run_date=_normalize_job_run_at(run_at)),
            id=_defer_id(slot_key),
            replace_existing=True,
            kwargs={
                "schedule_id": schedule_id,
                "fired_at_iso": fire_ctx.fired_at.isoformat(),
                "deadline_iso": fire_ctx.deadline.isoformat(),
                "defer_kind": fire_ctx.defer_kind,
            },
            misfire_grace_time=_defer_misfire_grace_seconds(),
        )
        fire_trace(
            "Manual defer job added schedule=%s job=%s run_at=%s defer_kind=%s",
            schedule_id,
            _defer_id(slot_key),
            run_at.isoformat(),
            fire_ctx.defer_kind,
        )

    # Only the HTTP "trigger now" call should return immediately while waiting for
    # COS clear. Defer retries run on the scheduler thread and block in
    # wait_for_transmit_clear so we do not stack 0.5s jobs that lose to
    # max_instances=1 or skipped_lock while synthesis holds the playback lock.
    defer_retry = fired_at_iso is not None or deadline_iso is not None
    if not defer_retry:
        clear_manual_trigger_drop_notice()
        fire_trace("Manual trigger started (HTTP) schedule=%s", schedule_id)
    else:
        fire_trace(
            "Manual trigger retry schedule=%s fired_at=%s deadline=%s",
            schedule_id,
            fired_at_iso,
            deadline_iso,
        )
    deps = FireDeps(
        now=now,
        radio=get_radio(),
        play_fn=play_chunks,
        sleep_fn=time.sleep,
        schedule_defer=defer,
        playback_lock=_playback_lock,
        synthesize=chunks_for_announcement,
        mark_last_run=lambda *_args: None,
        consume_baseline=lambda *_args: None,
        load=load,
        ptt_lead_seconds=PTT_LEAD_SECONDS,
        set_running=_set_running,
        clear_running=_clear_running,
        update_schedule=False,
        nonblocking_clear_wait=not defer_retry,
    )
    result = handle_fire(ctx, deps)
    if result == "skipped_lock":
        _clear_running()
        if announcement is not None and now < deadline:
            delay = defer_retry_delay(
                get_radio(),
                announcement,
                kind="busy",
                clear_seconds=DRA_CLEAR_SECONDS,
            )
            retry_at = now + timedelta(seconds=delay)
            defer(retry_at, replace(ctx, defer_kind="busy"))
            sync_jobs()
            jid = _defer_id(slot_key)
            sched = get_scheduler()
            if sched is None or sched.get_job(jid) is None:
                log.error("Manual defer job %s missing after skipped_lock retry", jid)
                sync_jobs()
                return _finish_manual_trigger(
                    schedule_id,
                    announcement,
                    schedule,
                    "dropped",
                    drop_reason="scheduler_error",
                )
            fire_trace(
                "Manual defer after skipped_lock schedule=%s job=%s at=%s",
                schedule_id,
                jid,
                retry_at.isoformat(),
            )
            log.info(
                "Manual trigger deferred; playback busy; retry job %s at %s",
                jid,
                retry_at.isoformat(),
            )
            return "deferred"
        log.info("Manual trigger dropped; playback lock held and past retry window")
        sync_jobs()
        return _finish_manual_trigger(
            schedule_id,
            announcement,
            schedule,
            "dropped",
            drop_reason="give_up_lock",
        )
    if result == "deferred":
        _clear_running()
        sync_jobs()
        sched = get_scheduler()
        jid = _defer_id(slot_key)
        if sched is None or sched.get_job(jid) is None:
            log.error("Manual defer job %s missing after sync_jobs", jid)
            return _finish_manual_trigger(
                schedule_id,
                announcement,
                schedule,
                "dropped",
                drop_reason="scheduler_error",
            )
        fire_trace("Manual defer scheduled schedule=%s job=%s", schedule_id, jid)
        log.info("Manual trigger deferred; retry job %s scheduled", jid)
        return result
    sync_jobs()
    if result == "dropped":
        log.info("Manual trigger dropped for schedule %s", schedule_id)
    return _finish_manual_trigger(schedule_id, announcement, schedule, result)


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
            key = _slot_key_from_defer_job_id(jid)
            if key is None:
                continue
            if key.startswith("manual:"):
                continue
            if _slot_job_id(key) not in wanted:
                scheduler.remove_job(jid)


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        sync_jobs()
        start_cos_tracking()
        return
    scheduler = PromptBackgroundScheduler(timezone=TIMEZONE, max_wait_seconds=SCHEDULER_MAX_WAIT_SECONDS)
    scheduler.start()
    _scheduler = scheduler
    _inhibit_idle_sleep()
    sync_jobs()
    start_cos_tracking()
    log.info("Scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    stop_cos_tracking()
    _scheduler.shutdown(wait=False)
    _scheduler = None
    _clear_running()
    _allow_idle_sleep()
    log.info("Scheduler stopped")
