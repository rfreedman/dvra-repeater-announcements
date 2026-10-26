"""Play a Newsline bulletin one piece at a time, yielding to an emergency between pieces."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from functools import partial

from app.audio import PcmChunk, materialize_chunks
from app.fire_trace import fire_trace
from app.models import Announcement, Schedule
from app.newsline.decode import mp3_to_chunks
from app.newsline.playlist import Piece
from app.newsline.session import PendingEmergency, bulletin_session
from app.radio import ChannelBusyError, TransmitAbortError, channel_blocks_transmit, transmit

log = logging.getLogger("app.newsline")


def _now(deps) -> datetime:
    wall = getattr(deps, "wall_now", None)
    if wall is not None:
        return wall()
    return deps.now


def _poll(deps) -> float:
    return deps.dra_clear_poll_seconds if deps.dra_clear_poll_seconds > 0 else 0.25


def _blocked(deps) -> bool:
    return channel_blocks_transmit(deps.radio, clear_seconds=deps.dra_clear_seconds)


def _fresh_busy(deps) -> bool:
    return bool(deps.radio.channel_busy())


def wait_until_clear(deps, *, deadline: datetime | None, gap_seconds: float = 0.0) -> str:
    """Wait out the unkey gap, then until the channel is clear.

    Returns ``clear``, ``emergency``, or ``deadline``.
    """
    remaining = gap_seconds
    poll = _poll(deps)
    while True:
        if bulletin_session.waiting():
            return "emergency"
        if deadline is not None and _now(deps) >= deadline:
            return "deadline"
        if remaining > 0:
            step = min(poll, remaining)
            deps.sleep_fn(step)
            remaining -= step
            continue
        if not _blocked(deps) and not _fresh_busy(deps):
            return "clear"
        deps.sleep_fn(poll)


def _chunks_for_piece(deps, piece: Piece) -> list[PcmChunk]:
    if piece.announcement is not None:
        return materialize_chunks(deps.synthesize(piece.announcement))
    if piece.path is None:
        raise ValueError(f"Newsline piece {piece.label} has no audio")
    return mp3_to_chunks(piece.path)


def _transmit_chunks(deps, chunks: Iterable[PcmChunk], *, on_key: Callable[[], None] | None) -> None:
    from app.fire import _RunningOnKeyRadio

    transmit(
        chunks,
        radio=_RunningOnKeyRadio(deps.radio, on_key),
        play_fn=deps.play_fn,
        sleep_fn=deps.sleep_fn,
        lead_seconds=deps.ptt_lead_seconds,
        clear_seconds=0,
    )


def _play_emergency(deps, pending: PendingEmergency) -> str:
    announcement = pending.announcement
    schedule = pending.schedule
    label = announcement.name
    deadline = _now(deps) + timedelta(seconds=announcement.busy_give_up_seconds)
    log.info("Newsline yielding to emergency %s", label)
    if deps.set_running:
        deps.set_running(announcement, schedule.id)
    outcome = wait_until_clear(deps, deadline=deadline, gap_seconds=0.0)
    if outcome != "clear":
        log.info("Emergency %s dropped; channel did not clear before its give-up", label)
        fire_trace("Emergency dropped during Newsline label=%s", label)
        return "dropped"
    on_key = partial(deps.set_running, announcement, schedule.id) if deps.set_running else None
    try:
        _transmit_chunks(deps, materialize_chunks(deps.synthesize(announcement)), on_key=on_key)
    except (TransmitAbortError, BrokenPipeError):
        log.warning("Emergency %s aborted; DRA USB lost", label)
        return "dropped_usb"
    except ChannelBusyError:
        log.info("Emergency %s dropped; channel busy at PTT", label)
        return "dropped"
    if deps.update_schedule:
        deps.mark_last_run(announcement.id, schedule.id, _now(deps))
    log.info("Emergency %s transmitted during Newsline", label)
    fire_trace("Emergency transmitted during Newsline label=%s", label)
    return "transmitted"


def _drain_emergencies(deps) -> str | None:
    while bulletin_session.waiting():
        pending = bulletin_session.take()
        if pending is None:
            break
        outcome = _play_emergency(deps, pending)
        if outcome == "dropped_usb":
            return outcome
    return None


def transmit_newsline(
    deps,
    pieces: list[Piece],
    *,
    bulletin: Announcement,
    schedule: Schedule,
    initial_deadline: datetime,
) -> str:
    """Play every piece. The initial clear wait still honors ``initial_deadline``."""
    label = bulletin.name
    outcome = wait_until_clear(deps, deadline=initial_deadline, gap_seconds=0.0)
    if outcome == "emergency":
        drained = _drain_emergencies(deps)
        if drained == "dropped_usb":
            return "dropped_usb"
    elif outcome == "deadline":
        if _now(deps) >= initial_deadline and _blocked(deps):
            log.info("Newsline deferred; %s; channel not clear before %s", label, initial_deadline.isoformat())
            return "not_clear"
        if _fresh_busy(deps):
            return "not_clear"
    on_key = partial(deps.set_running, bulletin, schedule.id) if deps.set_running else None
    if deps.set_running:
        deps.set_running(bulletin, schedule.id)
    gap = bulletin.ptt_gap_seconds
    for index, piece in enumerate(pieces):
        drained = _drain_emergencies(deps)
        if drained == "dropped_usb":
            return "dropped_usb"
        if index > 0 or bulletin_session.waiting():
            gap_outcome = wait_until_clear(deps, deadline=None, gap_seconds=gap)
            if gap_outcome == "emergency":
                drained = _drain_emergencies(deps)
                if drained == "dropped_usb":
                    return "dropped_usb"
                gap_outcome = wait_until_clear(deps, deadline=None, gap_seconds=gap)
            if gap_outcome != "clear":
                continue
        try:
            _transmit_chunks(deps, _chunks_for_piece(deps, piece), on_key=on_key)
        except (TransmitAbortError, BrokenPipeError):
            log.warning("Newsline aborted; %s; DRA USB lost during %s", label, piece.label)
            return "dropped_usb"
        except ChannelBusyError:
            log.info("Newsline waiting; %s; channel busy before %s", label, piece.label)
            gap_outcome = wait_until_clear(deps, deadline=None, gap_seconds=0.0)
            if gap_outcome == "emergency":
                _drain_emergencies(deps)
            try:
                _transmit_chunks(deps, _chunks_for_piece(deps, piece), on_key=on_key)
            except (TransmitAbortError, BrokenPipeError):
                return "dropped_usb"
            except ChannelBusyError:
                log.warning("Newsline skipped piece %s; channel stayed busy", piece.label)
                continue
        log.info("Newsline played %s", piece.label)
        if deps.set_running:
            deps.set_running(bulletin, schedule.id)
    drained = _drain_emergencies(deps)
    if drained == "dropped_usb":
        return "dropped_usb"
    log.info("Newsline transmitted; %s", label)
    fire_trace("Newsline transmitted label=%s", label)
    return "transmitted"
