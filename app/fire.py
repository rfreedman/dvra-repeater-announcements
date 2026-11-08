from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial

from app.audio import materialize_chunks
from app.fire_trace import fire_trace
from app.fire_types import (
    FireContext as FireContext,
    FireDeps as FireDeps,
    FireTrigger as FireTrigger,
    LoadedFire as LoadedFire,
    RunningOnKeyRadio,
    SetRunningFn as SetRunningFn,
    SetRunningWaitFn as SetRunningWaitFn,
)
from app.models import Announcement, Schedule
from app.radio_errors import ChannelBusyError, TransmitAbortError
from app.radio import (
    Radio,
    as_clear_wait,
    as_idle_credit,
    channel_blocks_transmit,
    transmit,
)

log = logging.getLogger("app.fire")

# Distinct from ``dropped`` (busy/give-up) so manual-trigger UI can show USB loss.
FIRE_OUTCOME_DROPPED_USB = "dropped_usb"

# Defer retry delays (seconds). See defer_retry_delay() and README “Defer timing”.
# Non-blocking manual clear wait: scheduler re-checks soon without blocking the HTTP thread.
DEFER_CLEAR_NONBLOCKING_MAX_SECONDS = 0.5
# When the clear hold is already met, retry soon instead of waiting the full busy_retry interval.
DEFER_RETRY_WHEN_CLEAR_MET_CAP_SECONDS = 1.0

def _fire_who(
    ctx: FireContext,
    schedule: Schedule | None = None,
    announcement: str | None = None,
) -> str:
    parts = [f"trigger={ctx.trigger}"]
    if schedule is not None:
        name = (schedule.name or "").strip() or schedule.id
        parts.append(f"schedule={name}")
    elif ctx.schedule_id:
        parts.append(f"schedule_id={ctx.schedule_id}")
    if announcement:
        parts.append(f"announcement={announcement}")
    return "; ".join(parts)


def clear_hold_met(radio: Radio, *, clear_seconds: float) -> bool:
    if clear_seconds <= 0:
        return True
    idle = as_idle_credit(radio)
    return idle is not None and idle.continuous_idle_seconds() >= clear_seconds


def defer_retry_delay(
    radio: Radio,
    announcement: Announcement,
    *,
    kind: str,
    clear_seconds: float,
    nonblocking_clear: bool = False,
) -> float:
    """Seconds until the next defer attempt (shorter when an enabled clear hold is already met)."""
    if kind == "clear" and nonblocking_clear:
        return min(DEFER_CLEAR_NONBLOCKING_MAX_SECONDS, float(announcement.busy_retry_seconds))
    base = float(announcement.busy_retry_seconds)
    if clear_seconds > 0 and clear_hold_met(radio, clear_seconds=clear_seconds):
        return min(base, DEFER_RETRY_WHEN_CLEAR_MET_CAP_SECONDS)
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
    schedule: Schedule,
    label: str,
    *,
    phase: str,
) -> str | None:
    if not channel_blocks_transmit(deps.radio, clear_seconds=deps.dra_clear_seconds):
        return None
    return _defer_or_drop_busy(deps, ctx, announcement, schedule, label, phase=phase)


def _defer_or_drop_busy(
    deps: FireDeps,
    ctx: FireContext,
    announcement: Announcement,
    schedule: Schedule,
    label: str,
    *,
    phase: str,
) -> str:
    who = _fire_who(ctx, schedule, label)
    if _wall_now(deps) >= ctx.deadline:
        log.info(
            "Fire dropped; %s; channel still busy %s (deadline %s)",
            who,
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
        who,
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


def _confirm_clear_before_ptt(
    deps: FireDeps,
    ctx: FireContext,
    announcement: Announcement,
    schedule: Schedule,
    label: str,
) -> str | None:
    """Fresh COS reads right before PTT; any busy sample defers or drops the fire.

    Unlike ``channel_blocks_transmit`` this never trusts accrued idle credit. With a clear
    hold it keeps reading for ``dra_final_check_seconds``, so a key-up as the UI countdown
    ends (the UI can lag the server by about a second) still stops the transmit.
    """
    poll = max(0.05, deps.dra_clear_poll_seconds)
    samples = 1
    if deps.dra_clear_seconds > 0 and deps.dra_final_check_seconds > 0:
        samples += math.ceil(deps.dra_final_check_seconds / poll)
    for index in range(samples):
        if index:
            deps.sleep_fn(poll)
        if deps.radio.channel_busy():
            return _defer_or_drop_busy(
                deps, ctx, announcement, schedule, label, phase="before PTT"
            )
    return None


def _wait_clear_or_outcome(
    deps: FireDeps,
    ctx: FireContext,
    announcement: Announcement,
    schedule: Schedule,
    label: str,
) -> str | None:
    clear_wait = as_clear_wait(deps.radio)
    if clear_wait is None or deps.dra_clear_seconds <= 0:
        return None
    if deps.nonblocking_clear_wait:
        idle = as_idle_credit(deps.radio)
        idle_ok = (
            idle is not None
            and not channel_blocks_transmit(deps.radio, clear_seconds=deps.dra_clear_seconds)
            and idle.continuous_idle_seconds() >= deps.dra_clear_seconds
        )
        who = _fire_who(ctx, schedule, label)
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
                who,
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
            who,
            retry_at.isoformat(),
        )
        fire_trace(
            "Fire deferred (clear, nonblocking) label=%s slot=%s retry_at=%s",
            label,
            ctx.slot_key,
            retry_at.isoformat(),
        )
        return "deferred"
    ok = clear_wait.wait_for_transmit_clear(
        clear_seconds=deps.dra_clear_seconds,
        poll_seconds=deps.dra_clear_poll_seconds,
        deadline=ctx.deadline,
        now=lambda: _wall_now(deps),
        sleep_fn=deps.sleep_fn,
        fail_fast_on_busy=False,
    )
    who = _fire_who(ctx, schedule, label)
    if ok:
        fire_trace("Fire clear wait satisfied label=%s slot=%s", label, ctx.slot_key)
        return None
    if _wall_now(deps) >= ctx.deadline:
        log.info(
            "Fire dropped; %s; no %ss clear before deadline %s",
            who,
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
        who,
        retry_at.isoformat(),
    )
    fire_trace(
        "Fire deferred (clear, blocking) label=%s slot=%s retry_at=%s",
        label,
        ctx.slot_key,
        retry_at.isoformat(),
    )
    return "deferred"


def _note_substitution(deps: FireDeps, ctx: FireContext, loaded: LoadedFire) -> None:
    if not loaded.skipped_detail:
        return
    log.info(
        "Fire substituted; %s; %s",
        _fire_who(ctx, loaded.schedule),
        loaded.skipped_detail,
    )
    from app.newsline.notices import record_skip_notice

    record_skip_notice(loaded.skipped_detail, when=deps.now)


def _newsline_not_ready(
    ctx: FireContext,
    schedule: Schedule,
    label: str,
) -> str | None:
    from app.newsline.playlist import newsline_unavailable_reason

    if newsline_unavailable_reason() is None:
        return None
    log.info(
        "Fire dropped; %s; Newsline is not available",
        _fire_who(ctx, schedule, label),
    )
    fire_trace("Fire dropped (newsline not ready) label=%s slot=%s", label, ctx.slot_key)
    return "dropped"


def _transmit_newsline(
    deps: FireDeps,
    ctx: FireContext,
    announcement: Announcement,
    schedule: Schedule,
) -> str:
    from app.newsline.playback import transmit_newsline
    from app.newsline.playlist import build_playlist, resume_piece
    from app.newsline.session import bulletin_session
    from app.newsline.state import load_state
    from app.store import get_store

    stories = load_state().stories_dir()
    if stories is None:
        return "dropped"
    try:
        document = get_store().document()
        pieces = build_playlist(announcement, document, stories)
        resume = resume_piece(announcement, document)
    except ValueError as exc:
        log.info(
            "Fire dropped; %s; %s",
            _fire_who(ctx, schedule, announcement.name),
            exc,
        )
        return "dropped"
    if not pieces:
        log.info(
            "Fire dropped; %s; Newsline playlist is empty",
            _fire_who(ctx, schedule, announcement.name),
        )
        return "dropped"
    bulletin_session.begin()
    try:
        outcome = transmit_newsline(
            deps,
            pieces,
            bulletin=announcement,
            schedule=schedule,
            initial_deadline=ctx.deadline,
            resume=resume,
        )
    finally:
        bulletin_session.end()
    if outcome == "not_clear":
        busy = _defer_or_drop_on_busy(
            deps,
            ctx,
            announcement,
            schedule,
            announcement.name,
            phase="before Newsline",
        )
        if busy:
            return busy
        if _wall_now(deps) >= ctx.deadline:
            return "dropped"
        delay = defer_retry_delay(deps.radio, announcement, kind="clear", clear_seconds=deps.dra_clear_seconds)
        deps.schedule_defer(_wall_now(deps) + timedelta(seconds=delay), replace(ctx, defer_kind="clear"))
        return "deferred"
    if outcome == "dropped_usb":
        return FIRE_OUTCOME_DROPPED_USB
    return outcome


def handle_fire(ctx: FireContext, deps: FireDeps) -> str:
    loaded = deps.load()
    if loaded is None:
        log.info("Fire skipped; %s; announcement or schedule missing", _fire_who(ctx))
        return "missing"
    announcement, schedule = loaded.announcement, loaded.schedule
    label = (announcement.name if announcement else None) or schedule.name or "Untitled"
    who = _fire_who(ctx, schedule, label if announcement is not None else None)
    if not schedule.enabled:
        log.info("Fire skipped; %s; schedule %s disabled", who, schedule.id)
        return "disabled"
    if loaded.silence:
        if deps.update_schedule:
            deps.mark_last_run(None, schedule.id, deps.now)
        log.info(
            "Fire skipped; %s; silence occupies slot %s",
            _fire_who(ctx, schedule, label),
            ctx.slot_key,
        )
        return "silence"
    _note_substitution(deps, ctx, loaded)
    if announcement is None:
        log.info(
            "Fire skipped; %s; announcement missing for schedule %s",
            _fire_who(ctx, schedule),
            schedule.id,
        )
        return "missing"

    who = _fire_who(ctx, schedule, label)
    log.info("Fire started; %s", who)
    if announcement.kind == "newsline":
        outcome = _newsline_not_ready(ctx, schedule, label)
        if outcome:
            return outcome

    outcome = _defer_or_drop_on_busy(
        deps, ctx, announcement, schedule, label, phase="at fire start"
    )
    if outcome:
        return outcome

    if not deps.playback_lock.acquire(blocking=False):
        from app.newsline.session import bulletin_session

        if bulletin_session.active and schedule.kind == "emergency":
            _ = bulletin_session.offer(announcement, schedule)
            log.info(
                "Emergency will play when the current Newsline piece finishes; %s",
                who,
            )
            fire_trace("Emergency queued behind Newsline label=%s slot=%s", label, ctx.slot_key)
            return "yielded"
        if bulletin_session.active:
            log.info("Fire dropped; %s; Newsline bulletin in progress", who)
            fire_trace("Fire dropped (newsline preempt) label=%s slot=%s", label, ctx.slot_key)
        else:
            log.info("Fire skipped; %s; another announcement is playing", who)
            fire_trace("Fire skipped_lock label=%s slot=%s", label, ctx.slot_key)
        return "skipped_lock"
    try:
        if announcement.kind == "newsline":
            outcome = _transmit_newsline(deps, ctx, announcement, schedule)
            if outcome != "transmitted":
                return outcome
            if deps.update_schedule:
                deps.mark_last_run(announcement.id, schedule.id, deps.now)
                if schedule.kind == "baseline":
                    deps.consume_baseline(schedule.id, ctx.slot_key)
            log.info("Fire transmitted; %s", who)
            return "transmitted"
        chunks = materialize_chunks(deps.synthesize(announcement))

        outcome = _defer_or_drop_on_busy(
            deps, ctx, announcement, schedule, label, phase="after synthesize"
        )
        if outcome:
            return outcome

        outcome = _wait_clear_or_outcome(deps, ctx, announcement, schedule, label)
        if outcome:
            return outcome

        outcome = _confirm_clear_before_ptt(deps, ctx, announcement, schedule, label)
        if outcome:
            return outcome

        on_key = partial(deps.set_running, announcement, schedule.id) if deps.set_running else None
        try:
            transmit(
                chunks,
                radio=RunningOnKeyRadio(deps.radio, on_key),
                play_fn=deps.play_fn,
                sleep_fn=deps.sleep_fn,
                lead_seconds=deps.ptt_lead_seconds,
                clear_seconds=deps.dra_clear_seconds,
            )
        except TransmitAbortError:
            log.warning("Fire aborted; %s; DRA USB lost during playback", who)
            fire_trace("Fire aborted (USB loss) label=%s slot=%s", label, ctx.slot_key)
            return FIRE_OUTCOME_DROPPED_USB
        except BrokenPipeError:
            log.warning(
                "Fire aborted; %s; DRA aplay pipe closed (USB unplugged during playback?)",
                who,
            )
            fire_trace("Fire aborted (USB loss) label=%s slot=%s", label, ctx.slot_key)
            return FIRE_OUTCOME_DROPPED_USB
        except ChannelBusyError:
            outcome = _defer_or_drop_on_busy(
                deps, ctx, announcement, schedule, label, phase="at PTT key"
            )
            if outcome:
                return outcome
            if _wall_now(deps) >= ctx.deadline:
                log.info(
                    "Fire dropped; %s; PTT blocked past deadline %s",
                    who,
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
                who,
                retry_at.isoformat(),
            )
            fire_trace(
                "Fire deferred (PTT) label=%s slot=%s retry_at=%s",
                label,
                ctx.slot_key,
                retry_at.isoformat(),
            )
            return "deferred"
        except Exception as exc:
            from app.dra_radio import dra_usb_transmit_lost

            if dra_usb_transmit_lost(deps.radio):
                log.warning(
                    "Fire aborted; %s; DRA USB lost during playback (%s)",
                    who,
                    exc,
                )
                fire_trace("Fire aborted (USB loss) label=%s slot=%s", label, ctx.slot_key)
                return FIRE_OUTCOME_DROPPED_USB
            raise
        if deps.update_schedule:
            deps.mark_last_run(announcement.id, schedule.id, deps.now)
            if schedule.kind == "baseline":
                deps.consume_baseline(schedule.id, ctx.slot_key)
        log.info("Fire transmitted; %s", who)
        fire_trace("Fire transmitted label=%s slot=%s", label, ctx.slot_key)
        return "transmitted"
    finally:
        if deps.clear_running:
            deps.clear_running()
        deps.playback_lock.release()
