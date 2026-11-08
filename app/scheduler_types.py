"""Typed shapes for APScheduler defer/manual jobs used by the announcer."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import NotRequired, Protocol, TypedDict

from app.fire_types import FireOutcome


class ScheduledDeferJobKwargs(TypedDict):
    slot_key: str
    fired_at_iso: str
    deadline_iso: str


class ManualDeferJobKwargs(TypedDict):
    schedule_id: str
    fired_at_iso: str
    deadline_iso: str
    defer_kind: NotRequired[str | None]


class SchedulerJob(Protocol):
    """Minimal APScheduler job surface we read (id, kwargs, next_run_time)."""

    @property
    def id(self) -> str | None: ...

    @property
    def kwargs(self) -> Mapping[str, object] | None: ...

    @property
    def next_run_time(self) -> datetime | None: ...


class ScheduledDeferFn(Protocol):
    def __call__(
        self,
        slot_key: str,
        fired_at_iso: str | None = None,
        deadline_iso: str | None = None,
    ) -> FireOutcome: ...


class ManualDeferFn(Protocol):
    def __call__(
        self,
        schedule_id: str,
        *,
        fired_at_iso: str | None = None,
        deadline_iso: str | None = None,
        defer_kind: str | None = None,
    ) -> FireOutcome: ...


def manual_job_deadline_iso(job: SchedulerJob) -> str | None:
    raw = (job.kwargs or {}).get("deadline_iso")
    return str(raw) if raw is not None else None


def manual_job_defer_kind(job: SchedulerJob) -> str | None:
    raw = (job.kwargs or {}).get("defer_kind")
    return str(raw) if raw is not None else None
