from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
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
from app.mac_idle import _allow_idle_sleep, _inhibit_idle_sleep
from app.manual_trigger_state import (
    MANUAL_NUDGE_MIN_WAIT_FOR_UNKNOWN_KIND_SECONDS,
    MANUAL_NUDGE_PTT_TIMER_PROTECT_SECONDS,
    MANUAL_NUDGE_RUN_DELAY_MS,
    MANUAL_NUDGE_SKIP_IF_RUN_WITHIN_SECONDS,
    _clear_manual_inflight,
    _manual_announcement_name,
    _pending_row_for_manual,
    _radio_status_for_ui,
    _set_manual_inflight,
    _set_manual_inflight_deadline,
    any_manual_trigger_inflight,
    clear_manual_trigger_drop_notice,
    nudge_manual_defer_jobs_if_clear_ready,
    peek_manual_trigger_drop_notice,
    pending_manual_triggers,
    record_manual_trigger_drop,
)
from app.manual_trigger_types import RunningTransmission
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

SCHEDULER_MIN_WAKE_SECONDS = 0.5  # PromptBackgroundScheduler sleep floor (macOS App Nap)

_scheduler: BackgroundScheduler | None = None
_playback_lock = threading.Lock()
_running_lock = threading.Lock()
_running: RunningTransmission | None = None


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
            skipped_detail=again.skipped_detail,
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
        document = store.document()
        from app.schedule_logic import peek_baseline
        from app.spoken_length import blocks_playback, too_long_parts

        if item is not None and blocks_playback(document, item):
            baseline = peek_baseline(document)
            fallback = (
                document.announcement_by_id(baseline.announcement_id)
                if baseline is not None and baseline.announcement_id
                else None
            )
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


def _job_still_scheduled_at(job, fire_at: datetime) -> bool:
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
    for item in resolve_window(document, now - lookback, until):
        if item.source == "none":
            continue
        jid = _slot_job_id(item.slot.key)
        if item.fire_at <= now:
            job = scheduler.get_job(jid)
            if job is not None and _job_still_scheduled_at(job, item.fire_at):
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
    from app.newsline.poll import install_news_poll

    install_news_poll()
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
