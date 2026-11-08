from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast, overload, override
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
from app.live_notify import notify_home_live_status
from app.mac_idle import allow_idle_sleep, inhibit_idle_sleep
from app.manual_trigger_state import (
    any_manual_trigger_inflight,
    clear_manual_inflight,
    clear_manual_trigger_drop_notice,
    manual_announcement_name,
    record_manual_trigger_drop,
    set_manual_inflight,
    set_manual_inflight_deadline,
)
from app.manual_trigger_types import RunningPhase, RunningTransmission
from app.models import Announcement, Schedule
from app.pcm_cache import chunks_for_announcement
from app.playback import play_chunks
from app.radio import get_radio
from app.schedule_logic import resolve_slot, resolve_window
from app.scheduler_instance import (
    DEFER_MISFIRE_GRACE_SECONDS as DEFER_MISFIRE_GRACE_SECONDS,
    MANUAL_KEY_PREFIX,
    SLOT_MISFIRE_GRACE_SECONDS,
    defer_job_id,
    get_scheduler as get_scheduler,
    parse_local,
    register_manual_trigger_runner,
    set_scheduler,
    slot_job_id,
    slot_key_from_defer_job_id,
)
from app.scheduler_types import (
    ManualDeferFn,
    SchedulerJob,
    ManualDeferJobKwargs,
    ScheduledDeferFn,
    ScheduledDeferJobKwargs,
)
from app.slots import as_local, slot_from_key, zone_for
from app.store import get_store

log = logging.getLogger("app.scheduler")


def _notify_home_live(*, force: bool = False) -> None:
    notify_home_live_status(force=force)


SCHEDULER_MIN_WAKE_SECONDS = 0.5  # PromptBackgroundScheduler sleep floor (macOS App Nap)

_playback_lock = threading.Lock()
_running_lock = threading.Lock()
_running: RunningTransmission | None = None


class PromptBackgroundScheduler(BackgroundScheduler):
    """Wake often enough that a long sleep cannot miss a fire by minutes.

    APScheduler otherwise waits until the next job. On macOS that wait can be
    App-Napped for well over the 30s misfire window.
    """

    max_wait_seconds: float

    def __init__(
        self,
        *args: object,
        max_wait_seconds: float = SCHEDULER_MAX_WAIT_SECONDS,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.max_wait_seconds = max(SCHEDULER_MIN_WAKE_SECONDS, float(max_wait_seconds))

    @override
    def _process_jobs(self) -> float | None:
        wait = super()._process_jobs()
        if wait is None:
            return self.max_wait_seconds
        return min(float(wait), self.max_wait_seconds)


def _station_zone() -> ZoneInfo:
    return zone_for(get_store().document().settings.timezone)


def get_running() -> RunningTransmission | None:
    with _running_lock:
        return RunningTransmission(**_running) if _running else None


def _set_running(
    announcement: Announcement,
    schedule_id: str,
    *,
    piece_label: str | None = None,
    story_index: int | None = None,
    story_total: int | None = None,
) -> None:
    global _running
    with _running_lock:
        _running = {
            "announcement_id": announcement.id,
            "announcement_name": announcement.name,
            "schedule_id": schedule_id,
            "started_at": datetime.now(_station_zone()).isoformat(),
            "phase": "playing",
            "clear_seconds": 0.0,
            "clear_remaining_seconds": None,
            "piece_label": piece_label,
            "story_index": story_index,
            "story_total": story_total,
        }
    _notify_home_live(force=True)


def _set_running_wait(
    *,
    phase: RunningPhase,
    clear_seconds: float = 0.0,
    clear_remaining_seconds: float | None = None,
    force: bool = False,
) -> None:
    """Update mid-bulletin wait phase (Newsline COS busy / clear hold) without clearing running."""
    global _running
    changed = False
    with _running_lock:
        if _running is None:
            return
        prev_phase = _running.get("phase") or "playing"
        prev_remaining = _running.get("clear_remaining_seconds")
        # Preserve piece_label / story_index / story_total across wait-phase updates.
        _running = {
            **_running,
            "phase": phase,
            "clear_seconds": float(clear_seconds),
            "clear_remaining_seconds": clear_remaining_seconds,
        }
        changed = (
            force
            or prev_phase != phase
            or (prev_remaining is None) != (clear_remaining_seconds is None)
            or (
                phase == "clear_window"
                and prev_remaining is not None
                and clear_remaining_seconds is not None
                and abs(float(prev_remaining) - float(clear_remaining_seconds)) >= 0.5
            )
        )
    if changed:
        _notify_home_live(force=True)


def _clear_running() -> None:
    global _running
    with _running_lock:
        _running = None
    # Manual trigger still holds inflight until run_manual_trigger's finally;
    # skip the intermediate notify so the UI does not flash pending between
    # Running and Waiting (that finally publishes with force=True).
    if any_manual_trigger_inflight():
        return
    _notify_home_live(force=True)


def _finish_manual_trigger(
    schedule_id: str,
    announcement: Announcement | None,
    schedule: Schedule | None,
    result: str,
    *,
    drop_reason: str | None = None,
) -> str:
    if result == "transmitted":
        clear_manual_trigger_drop_notice()
        # Inflight is still set; run_manual_trigger finally notifies after clear.
    elif result == "dropped":
        record_manual_trigger_drop(
            schedule_id,
            announcement_name=manual_announcement_name(announcement, schedule),
            reason=drop_reason or "give_up",
        )
    fire_trace(
        "Manual trigger finished schedule=%s result=%s drop_reason=%s",
        schedule_id,
        result,
        drop_reason,
    )
    return result


def _fire_window(
    zone: ZoneInfo,
    *,
    fired_at_iso: str | None,
    deadline_iso: str | None,
    default_fired_at: datetime,
    announcement: Announcement | None,
) -> tuple[datetime, datetime]:
    """When this fire started, and when busy retries give up."""
    fired_at = parse_local(fired_at_iso, zone) if fired_at_iso else default_fired_at
    if deadline_iso:
        return fired_at, parse_local(deadline_iso, zone)
    give_up = (
        announcement.busy_give_up_seconds if announcement else DEFAULT_BUSY_GIVE_UP_SECONDS
    )
    return fired_at, fired_at + timedelta(seconds=give_up)


@overload
def _add_defer_job(
    func: ScheduledDeferFn,
    slot_key: str,
    run_at: datetime,
    kwargs: ScheduledDeferJobKwargs,
) -> bool: ...


@overload
def _add_defer_job(
    func: ManualDeferFn,
    slot_key: str,
    run_at: datetime,
    kwargs: ManualDeferJobKwargs,
) -> bool: ...


def _add_defer_job(
    func: ScheduledDeferFn | ManualDeferFn,
    slot_key: str,
    run_at: datetime,
    kwargs: ScheduledDeferJobKwargs | ManualDeferJobKwargs,
) -> bool:
    sched = get_scheduler()
    if sched is None:
        log.error("Cannot defer fire for %s: scheduler not running", slot_key)
        return False
    _ = sched.add_job(
        func,
        DateTrigger(run_date=as_local(run_at, ZoneInfo(TIMEZONE))),
        id=defer_job_id(slot_key),
        replace_existing=True,
        kwargs=dict(kwargs),
        misfire_grace_time=DEFER_MISFIRE_GRACE_SECONDS,
    )
    return True


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
        trigger="scheduled",
    )

    def load() -> LoadedFire | None:
        again = resolve_slot(store.document(), slot)
        if again.schedule is None:
            return None
        return LoadedFire(
            announcement=again.announcement,
            schedule=again.schedule,
            silence=again.source == "silence",
            skipped_detail=again.skipped_detail,
        )

    def defer(run_at: datetime, fire_ctx: FireContext) -> None:
        defer_kwargs: ScheduledDeferJobKwargs = {
            "slot_key": fire_ctx.slot_key,
            "fired_at_iso": fire_ctx.fired_at.isoformat(),
            "deadline_iso": fire_ctx.deadline.isoformat(),
        }
        _ = _add_defer_job(run_scheduled_fire, slot_key, run_at, defer_kwargs)

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
        set_running_wait=_set_running_wait,
        not_ready_deadline=slot.center + timedelta(minutes=document.settings.slot_half_window_minutes),
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
    set_manual_inflight(schedule_id, manual_announcement_name(announcement, schedule))
    try:
        return _run_manual_trigger_body(
            schedule_id,
            schedule,
            announcement,
            fired_at_iso=fired_at_iso,
            deadline_iso=deadline_iso,
        )
    finally:
        clear_manual_inflight(schedule_id)
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
    set_manual_inflight_deadline(schedule_id, deadline)
    slot_key = f"{MANUAL_KEY_PREFIX}{schedule_id}"
    jid = defer_job_id(slot_key)
    ctx = FireContext(
        announcement_id=announcement.id if announcement else None,
        schedule_id=schedule.id,
        slot_key=slot_key,
        fired_at=fired_at,
        deadline=deadline,
        trigger="manual",
    )

    def load() -> LoadedFire | None:
        current = store.get_schedule(schedule_id)
        if current is None:
            return None
        item = store.get(current.announcement_id) if current.announcement_id else None
        document = store.document()
        from app.schedule_logic import announcement_cannot_play, peek_baseline
        from app.spoken_length import too_long_parts

        if item is not None and announcement_cannot_play(document, item):
            baseline = peek_baseline(document)
            fallback = (
                document.announcement_by_id(baseline.announcement_id)
                if baseline is not None and baseline.announcement_id
                else None
            )
            from app.newsline.playlist import newsline_unavailable_reason

            if item.kind == "newsline" and newsline_unavailable_reason():
                reason = "Newsline is not available"
            else:
                reason = "; ".join(too_long_parts(document, item)) or f"{item.name} cannot play"
            instead = (
                f"{fallback.name} will play instead."
                if fallback is not None
                else "The slot will be silent. No valid baseline is available."
            )
            detail = f"{current.name} will not play — {reason}. {instead}"
            if fallback is None or baseline is None:
                return LoadedFire(
                    announcement=None,
                    schedule=current,
                    silence=False,
                    skipped_detail=detail,
                )
            return LoadedFire(
                announcement=fallback,
                schedule=baseline,
                silence=False,
                skipped_detail=detail,
            )
        return LoadedFire(
            announcement=item,
            schedule=current,
            silence=current.is_silence,
        )

    def defer(run_at: datetime, fire_ctx: FireContext) -> None:
        defer_kwargs: ManualDeferJobKwargs = {
            "schedule_id": schedule_id,
            "fired_at_iso": fire_ctx.fired_at.isoformat(),
            "deadline_iso": fire_ctx.deadline.isoformat(),
            "defer_kind": fire_ctx.defer_kind,
        }
        added = _add_defer_job(run_manual_trigger, slot_key, run_at, defer_kwargs)
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
        mark_last_run=_no_last_run,
        consume_baseline=_no_baseline_consume,
        load=load,
        ptt_lead_seconds=PTT_LEAD_SECONDS,
        set_running=_set_running,
        clear_running=_clear_running,
        set_running_wait=_set_running_wait,
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


def _no_last_run(_announcement_id: str | None, _schedule_id: str, _when: datetime) -> None:
    """Manual triggers never advance a schedule's last-run."""


def _no_baseline_consume(_schedule_id: str, _slot_key: str) -> None:
    """Manual triggers never consume a baseline rotation slot."""


def _job_still_scheduled_at(job: SchedulerJob, fire_at: datetime) -> bool:
    """True when a pending job will still run at this fire time."""
    when = job.next_run_time
    if when is None:
        return False
    zone = fire_at.tzinfo
    if not isinstance(zone, ZoneInfo):
        return False
    return as_local(when, zone) == fire_at


def sync_jobs() -> None:
    """Make APScheduler's slot jobs match the store.

    Only *future* fire times get new jobs. A slot whose fire time has passed is
    kept only when its pending job is still scheduled for that same fire time
    (scheduler woke late). A job waiting at an older offset is removed and is
    never re-added — re-adding a past DateTrigger inside the misfire grace
    makes the slot transmit a second time.

    Jobs already scheduled at the correct fire time are left alone so a post-fire
    sync does not rewrite the whole lookahead window.
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
    wanted: set[str] = set()
    added = 0
    removed = 0
    for item in resolve_window(document, now - lookback, until):
        if item.source == "none":
            continue
        jid = slot_job_id(item.slot.key)
        if item.fire_at <= now:
            job = cast(SchedulerJob | None, scheduler.get_job(jid))
            if job is not None and _job_still_scheduled_at(job, item.fire_at):
                wanted.add(jid)
            continue
        wanted.add(jid)
        existing = cast(SchedulerJob | None, scheduler.get_job(jid))
        if existing is not None and _job_still_scheduled_at(existing, item.fire_at):
            continue
        _ = scheduler.add_job(
            run_scheduled_fire,
            DateTrigger(run_date=item.fire_at),
            id=jid,
            replace_existing=True,
            kwargs={"slot_key": item.slot.key},
            misfire_grace_time=SLOT_MISFIRE_GRACE_SECONDS,
        )
        added += 1
    for job in cast(list[SchedulerJob], scheduler.get_jobs()):
        jid = job.id or ""
        if jid.startswith("slot:") and jid not in wanted:
            scheduler.remove_job(jid)
            removed += 1
            continue
        key = slot_key_from_defer_job_id(jid)
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
            removed += 1
    if added or removed:
        log.info("Synced slot jobs; added %s, removed %s", added, removed)


def start_scheduler() -> None:
    if get_scheduler() is not None:
        sync_jobs()
        start_cos_tracking()
        return
    scheduler = PromptBackgroundScheduler(timezone=TIMEZONE, max_wait_seconds=SCHEDULER_MAX_WAIT_SECONDS)
    scheduler.start()
    set_scheduler(scheduler)
    inhibit_idle_sleep()
    sync_jobs()
    start_cos_tracking()
    from app.newsline.poll import install_news_poll

    install_news_poll()
    log.info("Scheduler started")


def stop_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler is None:
        return
    stop_cos_tracking()
    scheduler.shutdown(wait=False)
    set_scheduler(None)
    _clear_running()
    allow_idle_sleep()
    log.info("Scheduler stopped")


# Manual retries are re-added from app.manual_trigger_state, which cannot import this module.
register_manual_trigger_runner(run_manual_trigger)
