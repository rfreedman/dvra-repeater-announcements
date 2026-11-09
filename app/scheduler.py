"""Scheduler lifecycle façade: start/stop plus re-exports for existing imports."""

from __future__ import annotations

import logging
from typing import override

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import SCHEDULER_MAX_WAIT_SECONDS, TIMEZONE
from app.cos_track import start_cos_tracking, stop_cos_tracking
from app.mac_idle import allow_idle_sleep, inhibit_idle_sleep
from app import scheduler_fire as _scheduler_fire
from app.scheduler_instance import (
    DEFER_MISFIRE_GRACE_SECONDS as DEFER_MISFIRE_GRACE_SECONDS,
    MANUAL_KEY_PREFIX as MANUAL_KEY_PREFIX,
    SLOT_MISFIRE_GRACE_SECONDS as SLOT_MISFIRE_GRACE_SECONDS,
    defer_job_id as defer_job_id,
    get_scheduler as get_scheduler,
    parse_local as parse_local,
    register_manual_trigger_runner,
    register_scheduled_fire_runner,
    set_scheduler,
    slot_job_id as slot_job_id,
    slot_key_from_defer_job_id as slot_key_from_defer_job_id,
)
from app.scheduler_jobs import sync_jobs as sync_jobs

log = logging.getLogger("app.scheduler")

SCHEDULER_MIN_WAKE_SECONDS = 0.5  # PromptBackgroundScheduler sleep floor (macOS App Nap)

# Public names for helpers tests and the façade call. Implementations stay private.
get_running = _scheduler_fire.get_running
run_manual_trigger = _scheduler_fire.run_manual_trigger
run_scheduled_fire = _scheduler_fire.run_scheduled_fire
clear_running = _scheduler_fire._clear_running  # pyright: ignore[reportPrivateUsage]
finish_manual_trigger = _scheduler_fire._finish_manual_trigger  # pyright: ignore[reportPrivateUsage]
notify_home_live = _scheduler_fire._notify_home_live  # pyright: ignore[reportPrivateUsage]
playback_lock = _scheduler_fire._playback_lock  # pyright: ignore[reportPrivateUsage]
set_running = _scheduler_fire._set_running  # pyright: ignore[reportPrivateUsage]
set_running_wait = _scheduler_fire._set_running_wait  # pyright: ignore[reportPrivateUsage]


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

    def process_due_jobs(self) -> float | None:
        """Run one scheduler pass. Tests use this instead of ``_process_jobs``."""
        return self._process_jobs()


def wire_scheduler_runners() -> None:
    """Publish fire callables on the process runtime.

    Job modules cannot import this façade (that cycle is why the runners are
    registered here). Importing the façade registers them; startup registers
    them again so the lifespan path is explicit.
    """
    register_scheduled_fire_runner(run_scheduled_fire)
    register_manual_trigger_runner(run_manual_trigger)


def start_scheduler() -> None:
    wire_scheduler_runners()
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
    clear_running()
    allow_idle_sleep()
    log.info("Scheduler stopped")


# Job modules cannot import this façade, so runners are published on import.
wire_scheduler_runners()
