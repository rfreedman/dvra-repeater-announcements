"""Typed shapes for manual-trigger live status (REST/SSE home payload)."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

ManualTriggerPhase = Literal["clear_window", "channel_busy", "in_progress", "retry_pending"]


class RunningTransmission(TypedDict):
    announcement_id: str
    announcement_name: str
    schedule_id: str
    started_at: str


class ManualInflightEntry(TypedDict):
    schedule_id: str
    announcement_name: str
    started_at: str
    deadline_iso: NotRequired[str]


class PendingManualTriggerRow(TypedDict):
    schedule_id: str
    announcement_id: str | None
    announcement_name: str
    phase: ManualTriggerPhase
    clear_seconds: float
    clear_remaining_seconds: float | None
    give_up_remaining_seconds: float
    retry_in_seconds: float | None
    in_progress: bool
