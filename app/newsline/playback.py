"""Play a Newsline bulletin one piece at a time, yielding to an emergency between pieces."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from app.audio import PcmChunk, materialize_chunks
from app.fire_types import FireDeps, RunningOnKeyRadio
from app.fire_trace import fire_trace
from app.models import Announcement, Schedule
from app.newsline.decode import mp3_to_chunks
from app.newsline.playlist import Piece
from app.newsline.session import PendingEmergency, bulletin_session
from app.radio import (
    ChannelBusyError,
    TransmitAbortError,
    as_idle_credit,
    channel_blocks_transmit,
    transmit,
)

log = logging.getLogger("app.newsline")


@dataclass(frozen=True)
class ClearWait:
    outcome: str
    interrupted: bool = False


def _now(deps: FireDeps) -> datetime:
    return deps.now


def _poll(deps: FireDeps) -> float:
    return deps.dra_clear_poll_seconds if deps.dra_clear_poll_seconds > 0 else 0.25


def _blocked(deps: FireDeps) -> bool:
    return channel_blocks_transmit(deps.radio, clear_seconds=deps.dra_clear_seconds)


def _fresh_busy(deps: FireDeps) -> bool:
    return bool(deps.radio.channel_busy())


def _publish_running_wait(deps: FireDeps, *, busy: bool) -> None:
    """Show paused / clear-hold on the home card while Newsline waits mid-bulletin."""
    setter = deps.set_running_wait
    if setter is None:
        return
    clear_seconds = float(deps.dra_clear_seconds or 0)
    idle_credit = as_idle_credit(deps.radio)
    idle = float(idle_credit.continuous_idle_seconds()) if idle_credit is not None else 0.0
    if busy:
        setter(
            phase="channel_busy",
            clear_seconds=clear_seconds,
            clear_remaining_seconds=None,
        )
        return
    if clear_seconds > 0:
        setter(
            phase="clear_window",
            clear_seconds=clear_seconds,
            clear_remaining_seconds=max(0.0, clear_seconds - idle),
        )


def _publish_running_playing(deps: FireDeps) -> None:
    setter = deps.set_running_wait
    if setter is None:
        return
    setter(phase="playing", clear_seconds=0.0, clear_remaining_seconds=None)


def wait_until_clear(
    deps: FireDeps, *, deadline: datetime | None, gap_seconds: float = 0.0
) -> ClearWait:
    """Wait out the unkey gap, then until the channel is clear.

    ``interrupted`` is true when COS was busy after the unkey gap.
    After the gap, publishes running wait phases for the home UI while blocked.
    """
    remaining = gap_seconds
    poll = _poll(deps)
    interrupted = False
    published_wait = False
    while True:
        if bulletin_session.waiting():
            return ClearWait("emergency", interrupted)
        if deadline is not None and _now(deps) >= deadline:
            return ClearWait("deadline", interrupted)
        if remaining > 0:
            step = min(poll, remaining)
            deps.sleep_fn(step)
            remaining -= step
            continue
        busy = _fresh_busy(deps)
        if busy:
            interrupted = True
        if not busy and not _blocked(deps):
            if published_wait:
                _publish_running_playing(deps)
            return ClearWait("clear", interrupted)
        _publish_running_wait(deps, busy=busy)
        published_wait = True
        deps.sleep_fn(poll)


def _chunks_for_piece(deps: FireDeps, piece: Piece) -> list[PcmChunk]:
    if piece.announcement is not None:
        return materialize_chunks(deps.synthesize(piece.announcement))
    if piece.path is None:
        raise ValueError(f"Newsline piece {piece.label} has no audio")
    return mp3_to_chunks(piece.path)


def _transmit_chunks(
    deps: FireDeps, chunks: Iterable[PcmChunk], *, on_key: Callable[[], None] | None
) -> None:
    transmit(
        chunks,
        radio=RunningOnKeyRadio(deps.radio, on_key),
        play_fn=deps.play_fn,
        sleep_fn=deps.sleep_fn,
        lead_seconds=deps.ptt_lead_seconds,
        clear_seconds=0,
    )


def _play_emergency(deps: FireDeps, pending: PendingEmergency) -> str:
    announcement = pending.announcement
    schedule = pending.schedule
    label = announcement.name
    deadline = _now(deps) + timedelta(seconds=announcement.busy_give_up_seconds)
    log.info("Newsline yielding to emergency %s", label)
    if deps.set_running:
        deps.set_running(announcement, schedule.id)
    wait = wait_until_clear(deps, deadline=deadline, gap_seconds=0.0)
    if wait.outcome != "clear":
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


def _drain_emergencies(deps: FireDeps) -> str | None:
    while bulletin_session.waiting():
        pending = bulletin_session.take()
        if pending is None:
            break
        outcome = _play_emergency(deps, pending)
        if outcome == "dropped_usb":
            return outcome
    return None


def _is_story_piece(piece: Piece) -> bool:
    """True for split report MP3s (not opening / station ID / closing TTS)."""
    return piece.path is not None


def _transmit_piece(
    deps: FireDeps,
    piece: Piece,
    *,
    resume: Piece | None,
    with_resume: bool,
    on_key: Callable[[], None] | None,
) -> str | None:
    """Key once; optionally play resume then the piece without unkeying between."""
    chunks: list[PcmChunk] = []
    if with_resume and resume is not None:
        log.info("Newsline playing resume %s after channel busy before %s", resume.label, piece.label)
        chunks.extend(_chunks_for_piece(deps, resume))
    chunks.extend(_chunks_for_piece(deps, piece))
    try:
        _transmit_chunks(deps, chunks, on_key=on_key)
    except (TransmitAbortError, BrokenPipeError):
        return "dropped_usb"
    except ChannelBusyError:
        return "busy"
    return None


def transmit_newsline(
    deps: FireDeps,
    pieces: list[Piece],
    *,
    bulletin: Announcement,
    schedule: Schedule,
    initial_deadline: datetime,
    resume: Piece | None = None,
) -> str:
    """Play every piece. The initial clear wait still honors ``initial_deadline``."""
    label = bulletin.name
    wait = wait_until_clear(deps, deadline=initial_deadline, gap_seconds=0.0)
    if wait.outcome == "emergency":
        drained = _drain_emergencies(deps)
        if drained == "dropped_usb":
            return "dropped_usb"
    elif wait.outcome == "deadline":
        if _now(deps) >= initial_deadline and _blocked(deps):
            log.info("Newsline deferred; %s; channel not clear before %s", label, initial_deadline.isoformat())
            return "not_clear"
        if _fresh_busy(deps):
            return "not_clear"
    story_total = sum(1 for piece in pieces if _is_story_piece(piece))

    def _mark_running(
        piece: Piece,
        piece_index: int,
        *,
        display_label: str | None = None,
    ) -> None:
        if not deps.set_running:
            return
        piece_label = display_label if display_label is not None else piece.label
        story_index = None
        story_total_for_ui = None
        # Opening / station ID / closing / resume: name only. Stories get x of y.
        if display_label is None and _is_story_piece(piece):
            story_index = sum(1 for prior in pieces[: piece_index + 1] if _is_story_piece(prior))
            story_total_for_ui = story_total
        deps.set_running(
            bulletin,
            schedule.id,
            piece_label=piece_label,
            story_index=story_index,
            story_total=story_total_for_ui,
        )

    if deps.set_running:
        deps.set_running(bulletin, schedule.id)
    gap = bulletin.ptt_gap_seconds
    played_story = False
    for index, piece in enumerate(pieces):
        drained = _drain_emergencies(deps)
        if drained == "dropped_usb":
            return "dropped_usb"
        interrupted = False
        if index > 0 or bulletin_session.waiting():
            gap_wait = wait_until_clear(deps, deadline=None, gap_seconds=gap)
            interrupted = gap_wait.interrupted
            if gap_wait.outcome == "emergency":
                drained = _drain_emergencies(deps)
                if drained == "dropped_usb":
                    return "dropped_usb"
                gap_wait = wait_until_clear(deps, deadline=None, gap_seconds=gap)
                interrupted = interrupted or gap_wait.interrupted
            if gap_wait.outcome != "clear":
                continue
        # Resume only after a story has aired — not after opening-before-stories busy.
        with_resume = interrupted and played_story
        if with_resume and resume is not None:
            # Combined TX starts with resume (name only, not counted as a story item).
            _mark_running(piece, index, display_label=resume.label)
            on_key = (
                (lambda p=piece, i=index, lbl=resume.label: _mark_running(p, i, display_label=lbl))
                if deps.set_running
                else None
            )
        else:
            _mark_running(piece, index)
            on_key = (lambda p=piece, i=index: _mark_running(p, i)) if deps.set_running else None
        result = _transmit_piece(
            deps, piece, resume=resume, with_resume=with_resume, on_key=on_key
        )
        if result == "dropped_usb":
            log.warning("Newsline aborted; %s; DRA USB lost during %s", label, piece.label)
            return "dropped_usb"
        if result == "busy":
            log.info("Newsline waiting; %s; channel busy before %s", label, piece.label)
            gap_wait = wait_until_clear(deps, deadline=None, gap_seconds=0.0)
            if gap_wait.outcome == "emergency":
                _drain_emergencies(deps)
            # Same rule: resume only once stories have started.
            with_resume = played_story
            if with_resume and resume is not None:
                _mark_running(piece, index, display_label=resume.label)
                on_key = (
                    (lambda p=piece, i=index, lbl=resume.label: _mark_running(p, i, display_label=lbl))
                    if deps.set_running
                    else None
                )
            else:
                _mark_running(piece, index)
                on_key = (lambda p=piece, i=index: _mark_running(p, i)) if deps.set_running else None
            result = _transmit_piece(
                deps, piece, resume=resume, with_resume=with_resume, on_key=on_key
            )
            if result == "dropped_usb":
                return "dropped_usb"
            if result == "busy":
                log.warning("Newsline skipped piece %s; channel stayed busy", piece.label)
                continue
        log.info("Newsline played %s", piece.label)
        if _is_story_piece(piece):
            played_story = True
        _mark_running(piece, index)
    drained = _drain_emergencies(deps)
    if drained == "dropped_usb":
        return "dropped_usb"
    log.info("Newsline transmitted; %s", label)
    fire_trace("Newsline transmitted label=%s", label)
    return "transmitted"
