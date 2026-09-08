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

log = logging.getLogger("app.fire")


@dataclass
class FireContext:
    announcement_id: str | None
    schedule_id: str | None
    slot_key: str
    fired_at: datetime
    deadline: datetime


@dataclass
class LoadedFire:
    announcement: Announcement | None
    schedule: Schedule
    silence: bool


@dataclass
class FireDeps:
    now: datetime
    radio: Radio
    play_fn: Callable[[Iterable[PcmChunk]], None]
    sleep_fn: Callable[[float], None]
    schedule_defer: Callable[[datetime, FireContext], None]
    playback_lock: threading.Lock
    synthesize: Callable[[Announcement], Iterable[PcmChunk]]
    mark_last_run: Callable[[str | None, str, datetime], None]
    consume_baseline: Callable[[str, str], None]
    load: Callable[[], LoadedFire | None]
    ptt_lead_seconds: float = PTT_LEAD_SECONDS
    set_running: Callable[[Announcement, str], None] | None = None
    clear_running: Callable[[], None] | None = None


def handle_fire(ctx: FireContext, deps: FireDeps) -> str:
    loaded = deps.load()
    if loaded is None:
        log.info("Fire skipped; announcement or schedule missing")
        return "missing"
    announcement, schedule = loaded.announcement, loaded.schedule
    label = (announcement.name if announcement else None) or schedule.name or "Untitled"
    if not schedule.enabled:
        log.info("Fire skipped; %s; schedule %s disabled", label, schedule.id)
        return "disabled"
    if loaded.silence:
        deps.mark_last_run(None, schedule.id, deps.now)
        log.info("Fire skipped; %s; silence occupies slot %s", label, ctx.slot_key)
        return "silence"
    if announcement is None:
        log.info("Fire skipped; announcement missing for schedule %s", schedule.id)
        return "missing"
    if deps.radio.channel_busy():
        if deps.now >= ctx.deadline:
            log.info("Fire dropped; %s; channel still busy after %s", label, ctx.deadline.isoformat())
            return "dropped"
        retry_at = deps.now + timedelta(seconds=announcement.busy_retry_seconds)
        deps.schedule_defer(retry_at, ctx)
        log.info("Fire deferred; %s; retry at %s; channel busy", label, retry_at.isoformat())
        return "deferred"
    if not deps.playback_lock.acquire(blocking=False):
        log.info("Fire skipped; %s; another announcement is playing", label)
        return "skipped_lock"
    try:
        if deps.set_running:
            deps.set_running(announcement, schedule.id)
        chunks = deps.synthesize(announcement)
        transmit(
            chunks,
            radio=deps.radio,
            play_fn=deps.play_fn,
            sleep_fn=deps.sleep_fn,
            lead_seconds=deps.ptt_lead_seconds,
        )
        deps.mark_last_run(announcement.id, schedule.id, deps.now)
        if schedule.kind == "baseline":
            deps.consume_baseline(schedule.id, ctx.slot_key)
        log.info("Fire transmitted; %s", label)
        return "transmitted"
    finally:
        if deps.clear_running:
            deps.clear_running()
        deps.playback_lock.release()
