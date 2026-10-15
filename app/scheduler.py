from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable
from ctypes import POINTER, byref, c_char_p, c_int32, c_uint32, c_void_p
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from app.config import (
    DEFAULT_BUSY_GIVE_UP_SECONDS,
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
from app.manual_trigger_types import (
    ManualInflightEntry,
    ManualTriggerPhase,
    PendingManualTriggerRow,
    RunningTransmission,
)
from app.models import Announcement, Schedule
from app.pcm_cache import chunks_for_announcement
from app.playback import play_chunks
from app.radio import get_radio
from app.schedule_logic import resolve_slot, resolve_window
from app.slots import as_local, slot_from_key, zone_for
from app.store import get_store

log = logging.getLogger("app.scheduler")


def _notify_home_live(*, force: bool = False) -> None:
    from app.home_live import notify_home_live_status

    notify_home_live_status(force=force)


# A slot job may run this late (App Nap, busy host) and still fire.
SLOT_MISFIRE_GRACE_SECONDS = 30
DEFER_MISFIRE_GRACE_SECONDS = 120
MANUAL_KEY_PREFIX = "manual:"

# Manual defer nudge (nudge_manual_defer_jobs_if_clear_ready). See README “Defer timing”.
SCHEDULER_MIN_WAKE_SECONDS = 0.5  # PromptBackgroundScheduler sleep floor (macOS App Nap)
MANUAL_NUDGE_SKIP_IF_RUN_WITHIN_SECONDS = 0.2  # defer job already about to run
MANUAL_NUDGE_PTT_TIMER_PROTECT_SECONDS = 2.0  # leave short PTT retries on their timer
MANUAL_NUDGE_MIN_WAIT_FOR_UNKNOWN_KIND_SECONDS = 2.0  # only nudge unknown defer_kind if sooner
MANUAL_NUDGE_RUN_DELAY_MS = 50  # reschedule manual defer this soon after clear hold is met

_scheduler: BackgroundScheduler | None = None
_playback_lock = threading.Lock()
_idle_assertion: c_uint32 | None = None
_running_lock = threading.Lock()
_running: RunningTransmission | None = None
_manual_drop_lock = threading.Lock()
_manual_drop_notice: dict[str, object] | None = None
_manual_inflight_lock = threading.Lock()
_manual_inflight: dict[str, ManualInflightEntry] = {}

MANUAL_DROP_REASONS: dict[str, str] = {
    "give_up": "The channel did not stay clear long enough before the give-up timer expired.",
    "give_up_lock": "Another announcement was still playing when the give-up timer expired.",
    "scheduler_error": "The retry could not be scheduled (scheduler error).",
    "usb_loss": "The DRA USB interface was unplugged during the announcement.",
}


class PromptBackgroundScheduler(BackgroundScheduler):
    """Wake often enough that a long sleep cannot miss a fire by minutes.

    APScheduler otherwise waits until the next job. On macOS that wait can be
    App-Napped for well over the 30s misfire window.
    """

    def __init__(self, *args, max_wait_seconds: float = SCHEDULER_MAX_WAIT_SECONDS, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_wait_seconds = max(SCHEDULER_MIN_WAKE_SECONDS, float(max_wait_seconds))

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


def _station_zone() -> ZoneInfo:
    return zone_for(get_store().document().settings.timezone)


def get_running() -> RunningTransmission | None:
    with _running_lock:
        return dict(_running) if _running else None


def _set_running(announcement, schedule_id: str) -> None:
    global _running
    with _running_lock:
        _running = {
            "announcement_id": announcement.id,
            "announcement_name": announcement.name,
            "schedule_id": schedule_id,
            "started_at": datetime.now(_station_zone()).isoformat(),
        }
    _notify_home_live(force=True)


def _clear_running() -> None:
    global _running
    with _running_lock:
        _running = None
    _notify_home_live(force=True)


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
            "at": datetime.now(_station_zone()).isoformat(),
        }
    _notify_home_live(force=True)
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
        _notify_home_live(force=True)
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


def _manual_schedule_id(jid: str) -> str | None:
    key = _slot_key_from_defer_job_id(jid)
    if key is None or not key.startswith(MANUAL_KEY_PREFIX):
        return None
    return key[len(MANUAL_KEY_PREFIX) :]


def _parse_local(iso: str, zone: ZoneInfo) -> datetime:
    return as_local(datetime.fromisoformat(iso), zone)


def _fire_window(
    zone: ZoneInfo,
    *,
    fired_at_iso: str | None,
    deadline_iso: str | None,
    default_fired_at: datetime,
    announcement: Announcement | None,
) -> tuple[datetime, datetime]:
    """When this fire started, and when busy retries give up."""
    fired_at = _parse_local(fired_at_iso, zone) if fired_at_iso else default_fired_at
    if deadline_iso:
        return fired_at, _parse_local(deadline_iso, zone)
    give_up = (
        announcement.busy_give_up_seconds if announcement else DEFAULT_BUSY_GIVE_UP_SECONDS
    )
    return fired_at, fired_at + timedelta(seconds=give_up)


def _add_defer_job(
    func: Callable[..., str],
    slot_key: str,
    run_at: datetime,
    kwargs: dict[str, object],
) -> bool:
    sched = get_scheduler()
    if sched is None:
        log.error("Cannot defer fire for %s: scheduler not running", slot_key)
        return False
    sched.add_job(
        func,
        DateTrigger(run_date=as_local(run_at, ZoneInfo(TIMEZONE))),
        id=_defer_id(slot_key),
        replace_existing=True,
        kwargs=kwargs,
        misfire_grace_time=DEFER_MISFIRE_GRACE_SECONDS,
    )
    return True


def _set_manual_inflight(schedule_id: str, announcement_name: str) -> None:
    with _manual_inflight_lock:
        _manual_inflight[schedule_id] = {
            "schedule_id": schedule_id,
            "announcement_name": announcement_name,
            "started_at": datetime.now(_station_zone()).isoformat(),
        }
    _notify_home_live()


def _set_manual_inflight_deadline(schedule_id: str, deadline: datetime) -> None:
    with _manual_inflight_lock:
        entry = _manual_inflight.get(schedule_id)
        if entry is not None:
            entry["deadline_iso"] = deadline.isoformat()


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
    busy: bool,
    idle: float,
    clear_seconds: float,
    give_up_remaining: float,
    retry_in: float | None,
    in_progress: bool,
) -> PendingManualTriggerRow:
    # Radio state wins over in_progress: an in-flight retry spends most of its time
    # blocked in the clear-hold wait, and the UI should show that countdown.
    phase: ManualTriggerPhase
    clear_remaining: float | None
    if clear_seconds > 0 and idle >= clear_seconds:
        phase = "clear_window"
        clear_remaining = 0.0
    elif busy:
        phase = "channel_busy"
        clear_remaining = None
    elif clear_seconds > 0:
        phase = "clear_window"
        clear_remaining = max(0.0, clear_seconds - idle)
    elif in_progress:
        phase = "in_progress"
        clear_remaining = None
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
    """Run queued manual retries as soon as the clear hold is satisfied (not on retry timer).

    Called from the background COS poll thread, never from HTTP handlers.
    """
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
        if _manual_schedule_id(job.id or "") is None:
            continue
        next_run = job.next_run_time
        if next_run is None:
            continue
        wait_s = (as_local(next_run, zone) - now).total_seconds()
        if wait_s <= MANUAL_NUDGE_SKIP_IF_RUN_WITHIN_SECONDS:
            continue
        kwargs = dict(job.kwargs or {})
        defer_kind = kwargs.get("defer_kind")
        # Short PTT retries (~1s) should run on their timer, not yanked to MANUAL_NUDGE_RUN_DELAY_MS.
        if defer_kind == "ptt" and wait_s <= MANUAL_NUDGE_PTT_TIMER_PROTECT_SECONDS:
            continue
        if (
            wait_s > MANUAL_NUDGE_MIN_WAIT_FOR_UNKNOWN_KIND_SECONDS
            and defer_kind not in ("busy", "clear", "ptt")
        ):
            continue
        sched.add_job(
            run_manual_trigger,
            DateTrigger(run_date=now + timedelta(milliseconds=MANUAL_NUDGE_RUN_DELAY_MS)),
            id=job.id,
            replace_existing=True,
            kwargs=kwargs,
            misfire_grace_time=DEFER_MISFIRE_GRACE_SECONDS,
        )
        fire_trace(
            "Nudged manual defer job %s (idle=%.1fs, was %.1fs until run)",
            job.id,
            idle,
            wait_s,
        )
        return


def _radio_status_for_ui() -> tuple[bool, float, float]:
    """(busy, idle seconds, clear hold seconds) without disturbing COS where possible."""
    radio = get_radio()
    wait_clear = getattr(radio, "wait_for_transmit_clear", None)
    clear_seconds = (
        float(DRA_CLEAR_SECONDS) if wait_clear is not None and DRA_CLEAR_SECONDS > 0 else 0.0
    )
    snapshot = getattr(radio, "cos_defer_snapshot", None)
    if snapshot is not None:
        last_busy, idle = snapshot()
        return (True if last_busy is None else bool(last_busy)), float(idle), clear_seconds
    idle_fn = getattr(radio, "continuous_idle_seconds", None)
    busy = bool(radio.channel_busy())
    idle = float(idle_fn()) if idle_fn is not None else 0.0
    return busy, idle, clear_seconds


def pending_manual_triggers() -> list[PendingManualTriggerRow]:
    """Live status for manual Trigger now retries (busy defer / clear-window wait)."""
    sched = get_scheduler()
    deferred: dict[str, object] = {}
    if sched is not None:
        for job in sched.get_jobs():
            schedule_id = _manual_schedule_id(job.id or "")
            if schedule_id is not None:
                deferred[schedule_id] = job
    with _manual_inflight_lock:
        inflight_deadlines = {
            key: entry.get("deadline_iso") for key, entry in _manual_inflight.items()
        }
    inflight = list(inflight_deadlines)
    schedule_ids = list(deferred) + [item for item in inflight if item not in deferred]
    if not schedule_ids:
        return []

    document = get_store().document()
    zone = zone_for(document.settings.timezone)
    now = datetime.now(zone)
    busy, idle, clear_seconds = _radio_status_for_ui()
    out: list[PendingManualTriggerRow] = []
    for schedule_id in schedule_ids:
        schedule = document.schedule_by_id(schedule_id)
        if schedule is None:
            continue
        announcement = document.announcement_by_id(schedule.announcement_id)
        in_progress = schedule_id in inflight
        job = deferred.get(schedule_id)
        give_up_remaining = 0.0
        retry_in: float | None = 0.0
        deadline_iso = inflight_deadlines.get(schedule_id)
        if job is not None:
            deadline_iso = (job.kwargs or {}).get("deadline_iso") or deadline_iso
        if deadline_iso:
            deadline = _parse_local(str(deadline_iso), zone)
            give_up_remaining = max(0.0, (deadline - now).total_seconds())
        if job is not None and not in_progress:
            next_run = job.next_run_time
            retry_in = (
                max(0.0, (as_local(next_run, zone) - now).total_seconds())
                if next_run is not None
                else None
            )
        out.append(
            _pending_row_for_manual(
                schedule_id,
                announcement,
                schedule,
                busy=busy,
                idle=idle,
                clear_seconds=clear_seconds,
                give_up_remaining=give_up_remaining,
                retry_in=retry_in,
                in_progress=in_progress,
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
    fired_at, deadline = _fire_window(
        zone,
        fired_at_iso=fired_at_iso,
        deadline_iso=deadline_iso,
        default_fired_at=resolved.fire_at,
        announcement=resolved.announcement,
    )
    ctx = FireContext(
        announcement_id=resolved.announcement.id if resolved.announcement else None,
        schedule_id=resolved.schedule.id if resolved.schedule else None,
        slot_key=slot.key,
        fired_at=fired_at,
        deadline=deadline,
    )

    def load() -> LoadedFire | None:
        again = resolve_slot(store.document(), slot)
        if again.schedule is None:
            return None
        return LoadedFire(
            announcement=again.announcement,
            schedule=again.schedule,
            silence=again.source == "silence",
        )

    def defer(run_at: datetime, fire_ctx: FireContext) -> None:
        _add_defer_job(
            run_scheduled_fire,
            slot_key,
            run_at,
            {
                "slot_key": fire_ctx.slot_key,
                "fired_at_iso": fire_ctx.fired_at.isoformat(),
                "deadline_iso": fire_ctx.deadline.isoformat(),
            },
        )

    deps = FireDeps(
        now=datetime.now(zone),
        radio=get_radio(),
        play_fn=play_chunks,
        sleep_fn=time.sleep,
        schedule_defer=defer,
        playback_lock=_playback_lock,
        synthesize=chunks_for_announcement,
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
        _notify_home_live(force=True)


def _run_manual_trigger_body(
    schedule_id: str,
    schedule: Schedule,
    announcement: Announcement | None,
    *,
    fired_at_iso: str | None,
    deadline_iso: str | None,
) -> str:
    store = get_store()
    zone = zone_for(store.document().settings.timezone)
    now = datetime.now(zone)
    fired_at, deadline = _fire_window(
        zone,
        fired_at_iso=fired_at_iso,
        deadline_iso=deadline_iso,
        default_fired_at=now,
        announcement=announcement,
    )
    _set_manual_inflight_deadline(schedule_id, deadline)
    slot_key = f"{MANUAL_KEY_PREFIX}{schedule_id}"
    jid = _defer_id(slot_key)
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
        added = _add_defer_job(
            run_manual_trigger,
            slot_key,
            run_at,
            {
                "schedule_id": schedule_id,
                "fired_at_iso": fire_ctx.fired_at.isoformat(),
                "deadline_iso": fire_ctx.deadline.isoformat(),
                "defer_kind": fire_ctx.defer_kind,
            },
        )
        if added:
            fire_trace(
                "Manual defer job added schedule=%s job=%s run_at=%s defer_kind=%s",
                schedule_id,
                jid,
                run_at.isoformat(),
                fire_ctx.defer_kind,
            )

    def defer_job_missing() -> bool:
        sched = get_scheduler()
        return sched is None or sched.get_job(jid) is None

    # Only the HTTP "trigger now" call should return immediately while waiting for
    # COS clear. Defer retries run on the scheduler thread and block in
    # wait_for_transmit_clear so we do not stack short defer jobs that lose to
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
        if announcement is None or now >= deadline:
            log.info("Manual trigger dropped; playback lock held and past retry window")
            return _finish_manual_trigger(
                schedule_id, announcement, schedule, "dropped", drop_reason="give_up_lock"
            )
        delay = defer_retry_delay(
            get_radio(),
            announcement,
            kind="busy",
            clear_seconds=DRA_CLEAR_SECONDS,
        )
        retry_at = now + timedelta(seconds=delay)
        defer(retry_at, replace(ctx, defer_kind="busy"))
        if defer_job_missing():
            log.error("Manual defer job %s missing after skipped_lock retry", jid)
            return _finish_manual_trigger(
                schedule_id, announcement, schedule, "dropped", drop_reason="scheduler_error"
            )
        log.info(
            "Manual trigger deferred; playback busy; retry job %s at %s",
            jid,
            retry_at.isoformat(),
        )
        return "deferred"
    if result == "deferred":
        _clear_running()
        if defer_job_missing():
            log.error("Manual defer job %s missing", jid)
            return _finish_manual_trigger(
                schedule_id, announcement, schedule, "dropped", drop_reason="scheduler_error"
            )
        log.info("Manual trigger deferred; retry job %s scheduled", jid)
        return result
    drop_reason: str | None = None
    from app.fire import FIRE_OUTCOME_DROPPED_USB

    if result == FIRE_OUTCOME_DROPPED_USB:
        log.info("Manual trigger dropped for schedule %s (DRA USB unplugged)", schedule_id)
        drop_reason = "usb_loss"
        result = "dropped"
    elif result == "dropped":
        log.info("Manual trigger dropped for schedule %s", schedule_id)
    return _finish_manual_trigger(
        schedule_id,
        announcement,
        schedule,
        result,
        drop_reason=drop_reason,
    )


def sync_jobs() -> None:
    """Make APScheduler's slot jobs match the store.

    Only *future* fire times get new jobs. A slot whose fire time has passed is
    kept if its job is still pending (scheduler woke late) but is never re-added
    once APScheduler has run and removed it — re-adding a past DateTrigger inside
    the misfire grace makes the slot transmit a second time.
    """
    scheduler = get_scheduler()
    if scheduler is None:
        return
    document = get_store().document()
    zone = zone_for(document.settings.timezone)
    now = datetime.now(zone)
    until = now + timedelta(hours=LOOKAHEAD_HOURS)
    # Overlays fire up to the slot half-window after their slot center.
    lookback = timedelta(
        minutes=document.settings.slot_half_window_minutes,
        seconds=SLOT_MISFIRE_GRACE_SECONDS,
    )
    existing = {job.id for job in scheduler.get_jobs() if job.id}
    wanted: set[str] = set()
    for item in resolve_window(document, now - lookback, until):
        if item.source == "none":
            continue
        jid = _slot_job_id(item.slot.key)
        if item.fire_at <= now:
            if jid in existing:
                wanted.add(jid)
            continue
        wanted.add(jid)
        scheduler.add_job(
            run_scheduled_fire,
            DateTrigger(run_date=item.fire_at),
            id=jid,
            replace_existing=True,
            kwargs={"slot_key": item.slot.key},
            misfire_grace_time=SLOT_MISFIRE_GRACE_SECONDS,
        )
    for job in list(scheduler.get_jobs()):
        jid = job.id or ""
        if jid.startswith("slot:") and jid not in wanted:
            scheduler.remove_job(jid)
            continue
        key = _slot_key_from_defer_job_id(jid)
        if key is None or key.startswith(MANUAL_KEY_PREFIX):
            continue
        # Busy retries live until their own give-up deadline; drop them only when
        # the slot no longer has anything to transmit.
        try:
            source = resolve_slot(document, slot_from_key(key, zone)).source
        except ValueError:
            source = "none"
        if source in ("none", "silence"):
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
