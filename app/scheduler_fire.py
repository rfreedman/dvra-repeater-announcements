"""Scheduled and manual fire execution, running-transmission state, playback lock."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import config as app_config
from app.config import (
    DEFAULT_BUSY_GIVE_UP_SECONDS,
    DRA_CLEAR_SECONDS,
)
from app.fire import (
    FireContext,
    FireDeps,
    FireOutcome,
    LoadedFire,
    defer_retry_delay,
    handle_fire,
)
from app.fire_trace import fire_trace
from app.live_notify import notify_home_live_status
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
from app.schedule_logic import resolve_slot
from app.scheduler_instance import (
    MANUAL_KEY_PREFIX,
    defer_job_id,
    get_scheduler,
    parse_local,
)
from app.scheduler_jobs import add_defer_job, sync_jobs
from app.scheduler_types import ManualDeferJobKwargs, ScheduledDeferJobKwargs
from app.slots import slot_from_key, zone_for
from app.store import get_store

log = logging.getLogger("app.scheduler")

_playback_lock = threading.Lock()
_running_lock = threading.Lock()
_running: RunningTransmission | None = None


def _notify_home_live(*, force: bool = False) -> None:
    notify_home_live_status(force=force)


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
    result: FireOutcome,
    *,
    drop_reason: str | None = None,
) -> FireOutcome:
    if result == FireOutcome.TRANSMITTED:
        clear_manual_trigger_drop_notice()
        # Inflight is still set; run_manual_trigger finally notifies after clear.
    elif result == FireOutcome.DROPPED:
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


def run_scheduled_fire(
    slot_key: str,
    fired_at_iso: str | None = None,
    deadline_iso: str | None = None,
) -> FireOutcome:
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
        _ = add_defer_job(run_scheduled_fire, slot_key, run_at, defer_kwargs)

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
        ptt_lead_seconds=app_config.PTT_LEAD_SECONDS,
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
) -> FireOutcome:
    """Trigger now — same ``handle_fire`` / busy defer / clear wait as scheduled slots."""
    _ = defer_kind  # APScheduler job kwarg (nudge logic); overwritten on each defer
    store = get_store()
    schedule = store.get_schedule(schedule_id)
    if schedule is None:
        return FireOutcome.MISSING
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
) -> FireOutcome:
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
        added = add_defer_job(run_manual_trigger, slot_key, run_at, defer_kwargs)
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
        ptt_lead_seconds=app_config.PTT_LEAD_SECONDS,
        set_running=_set_running,
        clear_running=_clear_running,
        set_running_wait=_set_running_wait,
        update_schedule=False,
        nonblocking_clear_wait=not defer_retry,
    )
    result = handle_fire(ctx, deps)
    if result == FireOutcome.SKIPPED_LOCK:
        _clear_running()
        if announcement is None or now >= deadline:
            log.info("Manual trigger dropped; playback lock held and past retry window")
            return _finish_manual_trigger(
                schedule_id, announcement, schedule, FireOutcome.DROPPED, drop_reason="give_up_lock"
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
                schedule_id, announcement, schedule, FireOutcome.DROPPED, drop_reason="scheduler_error"
            )
        log.info(
            "Manual trigger deferred; playback busy; retry job %s at %s",
            jid,
            retry_at.isoformat(),
        )
        return FireOutcome.DEFERRED
    if result == FireOutcome.DEFERRED:
        _clear_running()
        if defer_job_missing():
            log.error("Manual defer job %s missing", jid)
            return _finish_manual_trigger(
                schedule_id, announcement, schedule, FireOutcome.DROPPED, drop_reason="scheduler_error"
            )
        log.info("Manual trigger deferred; retry job %s scheduled", jid)
        return result
    drop_reason: str | None = None
    if result == FireOutcome.DROPPED_USB:
        log.info("Manual trigger dropped for schedule %s (DRA USB unplugged)", schedule_id)
        drop_reason = "usb_loss"
        result = FireOutcome.DROPPED
    elif result == FireOutcome.DROPPED:
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
