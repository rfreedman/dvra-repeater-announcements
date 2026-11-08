"""APScheduler job inventory: sync slot jobs and defer add/remove helpers.

Fire *execution* lives in ``app.scheduler_fire``; this module only manages jobs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import cast, overload
from zoneinfo import ZoneInfo

from apscheduler.triggers.date import DateTrigger

from app.config import LOOKAHEAD_HOURS, TIMEZONE
from app.schedule_logic import resolve_slot, resolve_window
from app.scheduler_instance import (
    DEFER_MISFIRE_GRACE_SECONDS,
    MANUAL_KEY_PREFIX,
    SLOT_MISFIRE_GRACE_SECONDS,
    defer_job_id,
    get_scheduled_fire_runner,
    get_scheduler,
    slot_job_id,
    slot_key_from_defer_job_id,
)
from app.scheduler_types import (
    ManualDeferFn,
    ManualDeferJobKwargs,
    ScheduledDeferFn,
    ScheduledDeferJobKwargs,
    SchedulerJob,
)
from app.slots import as_local, slot_from_key, zone_for
from app.store import get_store

log = logging.getLogger("app.scheduler")


def _job_still_scheduled_at(job: SchedulerJob, fire_at: datetime) -> bool:
    """True when a pending job will still run at this fire time."""
    when = job.next_run_time
    if when is None:
        return False
    zone = fire_at.tzinfo
    if not isinstance(zone, ZoneInfo):
        return False
    return as_local(when, zone) == fire_at


@overload
def add_defer_job(
    func: ScheduledDeferFn,
    slot_key: str,
    run_at: datetime,
    kwargs: ScheduledDeferJobKwargs,
) -> bool: ...


@overload
def add_defer_job(
    func: ManualDeferFn,
    slot_key: str,
    run_at: datetime,
    kwargs: ManualDeferJobKwargs,
) -> bool: ...


def add_defer_job(
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
    runner = get_scheduled_fire_runner()
    if runner is None:
        log.error("Cannot sync slot jobs: scheduled fire runner not registered")
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
            runner,
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
