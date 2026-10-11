from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.audio import PcmChunk, materialize_chunks
from app.config import DRA_CLEAR_POLL_SECONDS, DRA_CLEAR_SECONDS, PTT_LEAD_SECONDS
from app.models import Announcement, Schedule
from app.fire_trace import fire_trace
from app.radio import ChannelBusyError, Radio, channel_blocks_transmit, transmit

log = logging.getLogger("app.fire")


@dataclass
class FireContext:
    announcement_id: str | None
    schedule_id: str | None
    slot_key: str
    fired_at: datetime
    deadline: datetime
    defer_kind: str | None = None


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
    dra_clear_seconds: float = DRA_CLEAR_SECONDS
    dra_clear_poll_seconds: float = DRA_CLEAR_POLL_SECONDS
    set_running: Callable[[Announcement, str], None] | None = None
    clear_running: Callable[[], None] | None = None
    update_schedule: bool = True
    nonblocking_clear_wait: bool = False


def clear_hold_met(radio: Radio, *, clear_seconds: float) -> bool:
    if clear_seconds <= 0:
        return True
    idle_fn = getattr(radio, "continuous_idle_seconds", None)
    return idle_fn is not None and idle_fn() >= clear_seconds


def defer_retry_delay(
    radio: Radio,
    announcement: Announcement,
    *,
    kind: str,
    clear_seconds: float,
    nonblocking_clear: bool = False,
) -> float:
    """Seconds until the next defer attempt (shorter when clear hold already met)."""
    if kind == "clear" and nonblocking_clear:
        return min(0.5, float(announcement.busy_retry_seconds))
    base = float(announcement.busy_retry_seconds)
    if clear_hold_met(radio, clear_seconds=clear_seconds):
        return min(base, 1.0)
    return base


def _wall_now(deps: FireDeps) -> datetime:
    """Live clock for give-up / clear waits (may differ from ``deps.now`` on defer retries)."""
    tzinfo = deps.now.tzinfo
    if tzinfo is not None:
        return datetime.now(tzinfo)
    return datetime.now().astimezone()


def _defer_or_drop_on_busy(
    deps: FireDeps,
    ctx: FireContext,
    announcement: Announcement,
    label: str,
    *,
    phase: str,
) -> str | None:
    if not channel_blocks_transmit(deps.radio, clear_seconds=deps.dra_clear_seconds):
        return None
    if _wall_now(deps) >= ctx.deadline:
        log.info(
            "Fire dropped; %s; channel still busy %s (deadline %s)",
            label,
            phase,
            ctx.deadline.isoformat(),
        )
        fire_trace(
            "Fire dropped (busy give-up) label=%s phase=%s slot=%s deadline=%s",
            label,
            phase,
            ctx.slot_key,
            ctx.deadline.isoformat(),
        )
        return "dropped"
    delay = defer_retry_delay(
        deps.radio,
        announcement,
        kind="busy",
        clear_seconds=deps.dra_clear_seconds,
    )
    retry_at = _wall_now(deps) + timedelta(seconds=delay)
    deps.schedule_defer(retry_at, replace(ctx, defer_kind="busy"))
    log.info(
        "Fire deferred; %s; retry at %s; channel busy %s",
        label,
        retry_at.isoformat(),
        phase,
    )
    fire_trace(
        "Fire deferred (busy) label=%s phase=%s slot=%s retry_at=%s",
        label,
        phase,
        ctx.slot_key,
        retry_at.isoformat(),
    )
    return "deferred"


def _wait_clear_or_outcome(
    deps: FireDeps,
    ctx: FireContext,
    announcement: Announcement,
    label: str,
) -> str | None:
    wait = getattr(deps.radio, "wait_for_transmit_clear", None)
    if wait is None or deps.dra_clear_seconds <= 0:
        return None
    if deps.nonblocking_clear_wait:
        idle_fn = getattr(deps.radio, "continuous_idle_seconds", None)
        idle_ok = (
            idle_fn is not None
            and not channel_blocks_transmit(deps.radio, clear_seconds=deps.dra_clear_seconds)
            and idle_fn() >= deps.dra_clear_seconds
        )
        if idle_ok:
            fire_trace(
                "Fire clear hold already satisfied label=%s slot=%s idle>=%ss",
                label,
                ctx.slot_key,
                deps.dra_clear_seconds,
            )
            return None
        if _wall_now(deps) >= ctx.deadline:
            log.info(
                "Fire dropped; %s; no %ss clear before deadline %s",
                label,
                deps.dra_clear_seconds,
                ctx.deadline.isoformat(),
            )
            fire_trace(
                "Fire dropped (clear give-up, nonblocking) label=%s slot=%s",
                label,
                ctx.slot_key,
            )
            return "dropped"
        delay = defer_retry_delay(
            deps.radio,
            announcement,
            kind="clear",
            clear_seconds=deps.dra_clear_seconds,
            nonblocking_clear=True,
        )
        retry_at = _wall_now(deps) + timedelta(seconds=delay)
        deps.schedule_defer(retry_at, replace(ctx, defer_kind="clear"))
        log.info(
            "Fire deferred; %s; retry at %s; clear wait on scheduler thread",
            label,
            retry_at.isoformat(),
        )
        fire_trace(
            "Fire deferred (clear, nonblocking) label=%s slot=%s retry_at=%s",
            label,
            ctx.slot_key,
            retry_at.isoformat(),
        )
        return "deferred"
    ok = wait(
        clear_seconds=deps.dra_clear_seconds,
        poll_seconds=deps.dra_clear_poll_seconds,
        deadline=ctx.deadline,
        now=lambda: _wall_now(deps),
        sleep_fn=deps.sleep_fn,
        fail_fast_on_busy=False,
    )
    if ok:
        fire_trace("Fire clear wait satisfied label=%s slot=%s", label, ctx.slot_key)
        return None
    if _wall_now(deps) >= ctx.deadline:
        log.info(
            "Fire dropped; %s; no %ss clear before deadline %s",
            label,
            deps.dra_clear_seconds,
            ctx.deadline.isoformat(),
        )
        fire_trace(
            "Fire dropped (clear give-up, blocking) label=%s slot=%s",
            label,
            ctx.slot_key,
        )
        return "dropped"
    delay = defer_retry_delay(
        deps.radio,
        announcement,
        kind="clear",
        clear_seconds=deps.dra_clear_seconds,
    )
    retry_at = _wall_now(deps) + timedelta(seconds=delay)
    deps.schedule_defer(retry_at, replace(ctx, defer_kind="clear"))
    log.info(
        "Fire deferred; %s; retry at %s; clear window not satisfied",
        label,
        retry_at.isoformat(),
    )
    fire_trace(
        "Fire deferred (clear, blocking) label=%s slot=%s retry_at=%s",
        label,
        ctx.slot_key,
        retry_at.isoformat(),
    )
    return "deferred"


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
        if deps.update_schedule:
            deps.mark_last_run(None, schedule.id, deps.now)
        log.info("Fire skipped; %s; silence occupies slot %s", label, ctx.slot_key)
        return "silence"
    if announcement is None:
        log.info("Fire skipped; announcement missing for schedule %s", schedule.id)
        return "missing"

    outcome = _defer_or_drop_on_busy(
        deps, ctx, announcement, label, phase="at fire start"
    )
    if outcome:
        return outcome

    if not deps.playback_lock.acquire(blocking=False):
        log.info("Fire skipped; %s; another announcement is playing", label)
        fire_trace("Fire skipped_lock label=%s slot=%s", label, ctx.slot_key)
        return "skipped_lock"
    try:
        chunks = materialize_chunks(deps.synthesize(announcement))

        outcome = _defer_or_drop_on_busy(
            deps, ctx, announcement, label, phase="after synthesize"
        )
        if outcome:
            return outcome

        outcome = _wait_clear_or_outcome(deps, ctx, announcement, label)
        if outcome:
            return outcome

        outcome = _defer_or_drop_on_busy(
            deps, ctx, announcement, label, phase="before PTT"
        )
        if outcome:
            return outcome

        class RunningOnKeyRadio:
            """Mark UI running only after PTT keys (DRA uses play_transmit_audio, not play_fn)."""

            def __init__(self, inner: Radio) -> None:
                self._inner = inner

            def __getattr__(self, name: str):
                return getattr(self._inner, name)

            def set_ptt(self, keyed: bool) -> None:
                self._inner.set_ptt(keyed)
                if keyed and deps.set_running:
                    deps.set_running(announcement, schedule.id)

        try:
            transmit(
                chunks,
                radio=RunningOnKeyRadio(deps.radio),
                play_fn=deps.play_fn,
                sleep_fn=deps.sleep_fn,
                lead_seconds=deps.ptt_lead_seconds,
                clear_seconds=deps.dra_clear_seconds,
            )
        except ChannelBusyError:
            outcome = _defer_or_drop_on_busy(
                deps, ctx, announcement, label, phase="at PTT key"
            )
            if outcome:
                return outcome
            if _wall_now(deps) >= ctx.deadline:
                log.info(
                    "Fire dropped; %s; PTT blocked past deadline %s",
                    label,
                    ctx.deadline.isoformat(),
                )
                fire_trace(
                    "Fire dropped (PTT give-up) label=%s slot=%s deadline=%s",
                    label,
                    ctx.slot_key,
                    ctx.deadline.isoformat(),
                )
                return "dropped"
            delay = defer_retry_delay(
                deps.radio,
                announcement,
                kind="ptt",
                clear_seconds=deps.dra_clear_seconds,
            )
            retry_at = _wall_now(deps) + timedelta(seconds=delay)
            deps.schedule_defer(retry_at, replace(ctx, defer_kind="ptt"))
            log.info(
                "Fire deferred; %s; retry at %s; PTT blocked (COS clear, key failed)",
                label,
                retry_at.isoformat(),
            )
            fire_trace(
                "Fire deferred (PTT) label=%s slot=%s retry_at=%s",
                label,
                ctx.slot_key,
                retry_at.isoformat(),
            )
            return "deferred"
        if deps.update_schedule:
            deps.mark_last_run(announcement.id, schedule.id, deps.now)
            if schedule.kind == "baseline":
                deps.consume_baseline(schedule.id, ctx.slot_key)
        log.info("Fire transmitted; %s", label)
        fire_trace("Fire transmitted label=%s slot=%s", label, ctx.slot_key)
        return "transmitted"
    finally:
        if deps.clear_running:
            deps.clear_running()
        if deps.playback_lock.locked():
            deps.playback_lock.release()
