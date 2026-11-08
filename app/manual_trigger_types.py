"""Typed shapes for manual-trigger and home live status (REST/SSE payloads)."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

ManualTriggerPhase = Literal["clear_window", "channel_busy", "in_progress", "retry_pending"]
RunningPhase = Literal["playing", "channel_busy", "clear_window"]


class RunningTransmission(TypedDict):
    announcement_id: str
    announcement_name: str
    schedule_id: str
    started_at: str
    phase: NotRequired[RunningPhase]
    clear_seconds: NotRequired[float]
    clear_remaining_seconds: NotRequired[float | None]
    piece_label: NotRequired[str | None]
    story_index: NotRequired[int | None]
    story_total: NotRequired[int | None]


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


class ManualDropNotice(TypedDict):
    schedule_id: str
    announcement_name: str
    reason: str
    detail: str
    at: str


class SkipNotice(TypedDict):
    detail: str
    at: str


class UpcomingRunning(TypedDict):
    announcement_id: str
    announcement_name: str
    schedule_id: str
    at: str
    status: Literal["running"]
    phase: RunningPhase
    clear_seconds: float
    clear_remaining_seconds: float | None
    piece_label: str | None
    story_index: int | None
    story_total: int | None
    note: str | None


class UpcomingWaiting(TypedDict):
    announcement_id: str
    announcement_name: str
    schedule_id: str | None
    schedule_name: str | None
    at: str
    status: Literal["waiting"]
    slot: str
    note: str | None


UpcomingRow = UpcomingRunning | UpcomingWaiting


class HomeLiveStatus(TypedDict):
    now: str
    upcoming: UpcomingRow | None
    pending_manual_triggers: list[PendingManualTriggerRow]
    manual_trigger_drop_notice: ManualDropNotice | None
    skip_notice: SkipNotice | None
    newsline_playing: bool


class SystemStats(TypedDict):
    celsius: float | None
    thermal_state: str | None
    cpu_percent: float | None
    memory_percent: float | None
