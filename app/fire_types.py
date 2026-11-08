"""Shapes a fire attempt is described by: context, loaded row, and injected deps.

Split out of ``app.fire`` so Newsline playback can take a ``FireDeps`` without
importing the module that calls it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast

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


class RunningOnKeyRadio:
    """Mark UI running only after PTT keys (DRA uses play_transmit_audio, not play_fn)."""

    _inner: Radio
    _on_key: Callable[[], None] | None

    def __init__(self, inner: Radio, on_key: Callable[[], None] | None) -> None:
        self._inner = inner
        self._on_key = on_key

    def __getattr__(self, name: str) -> object:
        return cast(object, getattr(self._inner, name))

    def channel_busy(self) -> bool:
        return self._inner.channel_busy()

    def set_ptt(self, keyed: bool) -> None:
        self._inner.set_ptt(keyed)
        if keyed and self._on_key is not None:
            self._on_key()
