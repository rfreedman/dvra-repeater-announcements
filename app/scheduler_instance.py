"""Job-id vocabulary and the scheduler slot on the process runtime.

Split out of ``app.scheduler`` so modules that only need to look at jobs
(manual trigger state, Newsline polling) do not have to import the scheduler
itself, which imports them back. The scheduler object lives on ``AppRuntime``.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.scheduler_types import ManualDeferFn, ScheduledDeferFn
from app.slots import as_local

# A slot job may run this late (App Nap, busy host) and still fire.
SLOT_MISFIRE_GRACE_SECONDS = 30
DEFER_MISFIRE_GRACE_SECONDS = 120
MANUAL_KEY_PREFIX = "manual:"

def get_scheduler() -> BackgroundScheduler | None:
    from app.runtime import get_runtime

    scheduler = get_runtime().scheduler
    if isinstance(scheduler, BackgroundScheduler):
        return scheduler
    return None


def set_scheduler(scheduler: BackgroundScheduler | None) -> None:
    from app.runtime import get_runtime

    get_runtime().scheduler = scheduler


def register_manual_trigger_runner(runner: ManualDeferFn) -> None:
    """``app.scheduler`` publishes its manual-trigger job here so retries can re-add it."""
    from app.runtime import get_runtime

    get_runtime().manual_trigger_runner = runner


def get_manual_trigger_runner() -> ManualDeferFn | None:
    from app.runtime import get_runtime

    runner = get_runtime().manual_trigger_runner
    if runner is None:
        return None
    return cast(ManualDeferFn, runner)


def register_scheduled_fire_runner(runner: ScheduledDeferFn) -> None:
    """``app.scheduler`` publishes slot fires so ``sync_jobs`` need not import fire code."""
    from app.runtime import get_runtime

    get_runtime().scheduled_fire_runner = runner


def get_scheduled_fire_runner() -> ScheduledDeferFn | None:
    from app.runtime import get_runtime

    runner = get_runtime().scheduled_fire_runner
    if runner is None:
        return None
    return cast(ScheduledDeferFn, runner)


def slot_job_id(slot_key: str) -> str:
    return f"slot:{slot_key}"


def defer_job_id(slot_key: str) -> str:
    return f"defer:{slot_key}"


def slot_key_from_defer_job_id(jid: str) -> str | None:
    if not jid.startswith("defer:"):
        return None
    return jid[len("defer:") :]


def manual_schedule_id(jid: str) -> str | None:
    key = slot_key_from_defer_job_id(jid)
    if key is None or not key.startswith(MANUAL_KEY_PREFIX):
        return None
    return key[len(MANUAL_KEY_PREFIX) :]


def parse_local(iso: str, zone: ZoneInfo) -> datetime:
    return as_local(datetime.fromisoformat(iso), zone)
