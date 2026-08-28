from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.audio import PcmChunk
from app.config import PTT_LEAD_SECONDS
from app.models import Announcement, Schedule
from app.radio import Radio, transmit
from app.schedule_logic import is_excluded

log = logging.getLogger("app.fire")


@dataclass
class FireContext:
    announcement_id: str
    schedule_id: str
    fired_at: datetime
    deadline: datetime


@dataclass
class FireDeps:
    now: datetime
    radio: Radio
    play_fn: Callable[[Iterable[PcmChunk]], None]
    sleep_fn: Callable[[float], None]
    schedule_defer: Callable[[datetime, FireContext], None]
    playback_lock: threading.Lock
    synthesize: Callable[[Announcement], Iterable[PcmChunk]]
    mark_last_run: Callable[[str, str, datetime], None]
    load: Callable[[], tuple[Announcement, Schedule] | None]
    ptt_lead_seconds: float = PTT_LEAD_SECONDS
    set_running: Callable[[Announcement, str], None] | None = None
    clear_running: Callable[[], None] | None = None


def handle_fire(ctx: FireContext, deps: FireDeps) -> str:
    loaded = deps.load()
    if loaded is None:
        log.info("Fire skipped; announcement or schedule missing")
        return "missing"
    announcement, schedule = loaded
    if not schedule.enabled:
        log.info("Fire skipped; schedule %s disabled", schedule.id)
        return "disabled"
    if is_excluded(schedule, deps.now):
        log.info("Fire skipped; excluded at %s", deps.now.isoformat())
        return "excluded"
    if deps.radio.channel_busy():
        if deps.now >= ctx.deadline:
            log.info("Fire dropped; channel still busy after %s", ctx.deadline.isoformat())
            return "dropped"
        retry_at = deps.now + timedelta(seconds=announcement.busy_retry_seconds)
        deps.schedule_defer(retry_at, ctx)
        log.info("Fire deferred to %s; channel busy", retry_at.isoformat())
        return "deferred"
    if not deps.playback_lock.acquire(blocking=False):
        log.info("Fire skipped; another announcement is playing")
        return "skipped_lock"
    try:
        if deps.set_running:
            deps.set_running(announcement, ctx.schedule_id)
        chunks = deps.synthesize(announcement)
        transmit(
            chunks,
            radio=deps.radio,
            play_fn=deps.play_fn,
            sleep_fn=deps.sleep_fn,
            lead_seconds=deps.ptt_lead_seconds,
        )
        deps.mark_last_run(ctx.announcement_id, ctx.schedule_id, deps.now)
        log.info("Fire transmitted; %s", announcement.name or "Untitled")
        return "transmitted"
    finally:
        if deps.clear_running:
            deps.clear_running()
        deps.playback_lock.release()
