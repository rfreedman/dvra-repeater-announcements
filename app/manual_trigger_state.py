"""In-flight manual triggers, drop notices, pending rows, and clear-hold nudge."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from apscheduler.triggers.date import DateTrigger

from app.config import DRA_CLEAR_SECONDS, TIMEZONE
from app.fire_trace import fire_trace
from app.live_notify import notify_home_live_status
from app.manual_trigger_types import (
    ManualDropNotice,
    ManualInflightEntry,
    ManualTriggerPhase,
    PendingManualTriggerRow,
)
from app.models import Announcement, Schedule
from app.radio import as_clear_wait, as_cos_snapshot, as_idle_credit, get_radio
from app.scheduler_instance import (
    DEFER_MISFIRE_GRACE_SECONDS,
    get_manual_trigger_runner,
    get_scheduler,
    manual_schedule_id,
    parse_local,
)
from app.scheduler_types import (
    SchedulerJob,
    manual_job_deadline_iso,
    manual_job_defer_kind,
)
from app.slots import as_local, zone_for
from app.store import get_store

log = logging.getLogger("app.scheduler")

# Manual defer nudge (nudge_manual_defer_jobs_if_clear_ready). See README “Defer timing”.
MANUAL_NUDGE_SKIP_IF_RUN_WITHIN_SECONDS = 0.2  # defer job already about to run
MANUAL_NUDGE_PTT_TIMER_PROTECT_SECONDS = 2.0  # leave short PTT retries on their timer
MANUAL_NUDGE_MIN_WAIT_FOR_UNKNOWN_KIND_SECONDS = 2.0  # only nudge unknown defer_kind if sooner
MANUAL_NUDGE_RUN_DELAY_MS = 50  # reschedule manual defer this soon after clear hold is met

_manual_drop_lock = threading.Lock()
_manual_drop_notice: ManualDropNotice | None = None
_manual_inflight_lock = threading.Lock()
_manual_inflight: dict[str, ManualInflightEntry] = {}

MANUAL_DROP_REASONS: dict[str, str] = {
    "give_up": "The channel did not stay clear long enough before the give-up timer expired.",
    "give_up_lock": "Another announcement was still playing when the give-up timer expired.",
    "scheduler_error": "The retry could not be scheduled (scheduler error).",
    "usb_loss": "The DRA USB interface was unplugged during the announcement.",
}


def _notify_home_live(*, force: bool = False) -> None:
    notify_home_live_status(force=force)


def _station_zone() -> ZoneInfo:
    return zone_for(get_store().document().settings.timezone)


def peek_manual_trigger_drop_notice() -> ManualDropNotice | None:
    with _manual_drop_lock:
        return ManualDropNotice(**_manual_drop_notice) if _manual_drop_notice else None


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


def manual_announcement_name(announcement: Announcement | None, schedule: Schedule | None) -> str:
    if announcement is not None and announcement.name:
        return announcement.name
    if schedule is not None and schedule.name:
        return schedule.name
    return "Untitled"


def set_manual_inflight(schedule_id: str, announcement_name: str) -> None:
    with _manual_inflight_lock:
        _manual_inflight[schedule_id] = {
            "schedule_id": schedule_id,
            "announcement_name": announcement_name,
            "started_at": datetime.now(_station_zone()).isoformat(),
        }
    _notify_home_live()


def set_manual_inflight_deadline(schedule_id: str, deadline: datetime) -> None:
    with _manual_inflight_lock:
        entry = _manual_inflight.get(schedule_id)
        if entry is not None:
            entry["deadline_iso"] = deadline.isoformat()


def clear_manual_inflight(schedule_id: str) -> None:
    with _manual_inflight_lock:
        _ = _manual_inflight.pop(schedule_id, None)


def any_manual_trigger_inflight() -> bool:
    with _manual_inflight_lock:
        return bool(_manual_inflight)


def pending_row_for_manual(
    schedule_id: str,
    announcement: Announcement | None,
    schedule: Schedule | None,
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
        "announcement_name": manual_announcement_name(announcement, schedule),
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
    run_manual_trigger = get_manual_trigger_runner()
    if sched is None or run_manual_trigger is None:
        return
    radio = get_radio()
    idle = as_idle_credit(radio)
    if idle is None:
        return
    idle_s = float(idle.continuous_idle_seconds())
    if idle_s < DRA_CLEAR_SECONDS:
        return
    zone = ZoneInfo(TIMEZONE)
    now = datetime.now(zone)
    for typed_job in cast(list[SchedulerJob], sched.get_jobs()):
        job_id = typed_job.id or ""
        if manual_schedule_id(job_id) is None:
            continue
        next_run = typed_job.next_run_time
        if next_run is None:
            continue
        wait_s = (as_local(next_run, zone) - now).total_seconds()
        if wait_s <= MANUAL_NUDGE_SKIP_IF_RUN_WITHIN_SECONDS:
            continue
        raw_kwargs = dict(typed_job.kwargs or {})
        defer_kind = manual_job_defer_kind(typed_job)
        # Short PTT retries (~1s) should run on their timer, not yanked to MANUAL_NUDGE_RUN_DELAY_MS.
        if defer_kind == "ptt" and wait_s <= MANUAL_NUDGE_PTT_TIMER_PROTECT_SECONDS:
            continue
        if (
            wait_s > MANUAL_NUDGE_MIN_WAIT_FOR_UNKNOWN_KIND_SECONDS
            and defer_kind not in ("busy", "clear", "ptt")
        ):
            continue
        schedule_id = str(raw_kwargs.get("schedule_id") or "") or manual_schedule_id(job_id)
        if not schedule_id:
            continue
        # Preserve original kwargs; only ensure schedule_id is present for the retry call.
        nudge_kwargs = dict(raw_kwargs)
        nudge_kwargs["schedule_id"] = schedule_id
        _ = sched.add_job(
            run_manual_trigger,
            DateTrigger(run_date=now + timedelta(milliseconds=MANUAL_NUDGE_RUN_DELAY_MS)),
            id=job_id,
            replace_existing=True,
            kwargs=nudge_kwargs,
            misfire_grace_time=DEFER_MISFIRE_GRACE_SECONDS,
        )
        fire_trace(
            "Nudged manual defer job %s (idle=%.1fs, was %.1fs until run)",
            job_id,
            idle_s,
            wait_s,
        )
        return


def radio_status_for_ui() -> tuple[bool, float, float]:
    """(busy, idle seconds, clear hold seconds) without disturbing COS where possible."""
    radio = get_radio()
    clear_wait = as_clear_wait(radio)
    clear_seconds = (
        float(DRA_CLEAR_SECONDS) if clear_wait is not None and DRA_CLEAR_SECONDS > 0 else 0.0
    )
    snapshot = as_cos_snapshot(radio)
    if snapshot is not None:
        last_busy, idle_s = snapshot.cos_defer_snapshot()
        return (True if last_busy is None else bool(last_busy)), float(idle_s), clear_seconds
    idle = as_idle_credit(radio)
    busy = bool(radio.channel_busy())
    idle_s = float(idle.continuous_idle_seconds()) if idle is not None else 0.0
    return busy, idle_s, clear_seconds


def pending_manual_triggers() -> list[PendingManualTriggerRow]:
    """Live status for manual Trigger now retries (busy defer / clear-window wait)."""
    sched = get_scheduler()
    deferred: dict[str, SchedulerJob] = {}
    if sched is not None:
        for typed_job in cast(list[SchedulerJob], sched.get_jobs()):
            schedule_id = manual_schedule_id(typed_job.id or "")
            if schedule_id is not None:
                deferred[schedule_id] = typed_job
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
    busy, idle, clear_seconds = radio_status_for_ui()
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
            deadline_iso = manual_job_deadline_iso(job) or deadline_iso
        if deadline_iso:
            deadline = parse_local(str(deadline_iso), zone)
            give_up_remaining = max(0.0, (deadline - now).total_seconds())
        if job is not None and not in_progress:
            next_run = job.next_run_time
            retry_in = (
                max(0.0, (as_local(next_run, zone) - now).total_seconds())
                if next_run is not None
                else None
            )
        out.append(
            pending_row_for_manual(
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
