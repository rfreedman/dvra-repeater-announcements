"""The APScheduler singleton and the job-id vocabulary built on top of it.

Split out of ``app.scheduler`` so modules that only need to look at jobs
(manual trigger state, Newsline polling) do not have to import the scheduler
itself, which imports them back.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.slots import as_local

# A slot job may run this late (App Nap, busy host) and still fire.
SLOT_MISFIRE_GRACE_SECONDS = 30
DEFER_MISFIRE_GRACE_SECONDS = 120
MANUAL_KEY_PREFIX = "manual:"

_scheduler: BackgroundScheduler | None = None
_manual_trigger_runner: Callable[..., str] | None = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def set_scheduler(scheduler: BackgroundScheduler | None) -> None:
    global _scheduler
    _scheduler = scheduler


def register_manual_trigger_runner(runner: Callable[..., str]) -> None:
    """``app.scheduler`` publishes its manual-trigger job here so retries can re-add it."""
    global _manual_trigger_runner
    _manual_trigger_runner = runner


def get_manual_trigger_runner() -> Callable[..., str] | None:
    return _manual_trigger_runner


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
