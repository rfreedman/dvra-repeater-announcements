"""Shapes a fire attempt is described by: context, loaded row, and injected deps.

Split out of ``app.fire`` so Newsline playback can take a ``FireDeps`` without
importing the module that calls it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from app.audio import PcmChunk
from app.config import (
    DRA_CLEAR_POLL_SECONDS,
    DRA_CLEAR_SECONDS,
    DRA_FINAL_CHECK_SECONDS,
    PTT_LEAD_SECONDS,
)
from app.manual_trigger_types import RunningPhase
from app.models import Announcement, Schedule
from app.radio import Radio

FireTrigger = Literal["scheduled", "manual"]


class FireOutcome(StrEnum):
    """Result of a fire attempt (scheduled, manual, or Newsline piece)."""

    TRANSMITTED = "transmitted"
    DEFERRED = "deferred"
    DROPPED = "dropped"
    DROPPED_USB = "dropped_usb"
    MISSING = "missing"
    SILENCE = "silence"
    DISABLED = "disabled"
    YIELDED = "yielded"
    SKIPPED_LOCK = "skipped_lock"
    BUSY = "busy"
    # Newsline: first piece could not start before the slot clear wait gave up.
    NOT_CLEAR = "not_clear"


class SetRunningFn(Protocol):
    def __call__(
        self,
        announcement: Announcement,
        schedule_id: str,
        *,
        piece_label: str | None = None,
        story_index: int | None = None,
        story_total: int | None = None,
    ) -> None: ...


class SetRunningWaitFn(Protocol):
    def __call__(
        self,
        *,
        phase: RunningPhase,
        clear_seconds: float = 0.0,
        clear_remaining_seconds: float | None = None,
        force: bool = False,
    ) -> None: ...


@dataclass
class FireContext:
    announcement_id: str | None
    schedule_id: str | None
    slot_key: str
    fired_at: datetime
    deadline: datetime
    trigger: FireTrigger
    defer_kind: str | None = None


@dataclass
class LoadedFire:
    announcement: Announcement | None
    schedule: Schedule
    silence: bool
    skipped_detail: str | None = None


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
    dra_final_check_seconds: float = DRA_FINAL_CHECK_SECONDS
    set_running: SetRunningFn | None = None
    clear_running: Callable[[], None] | None = None
    set_running_wait: SetRunningWaitFn | None = None
    update_schedule: bool = True
    nonblocking_clear_wait: bool = False
    not_ready_deadline: datetime | None = None
