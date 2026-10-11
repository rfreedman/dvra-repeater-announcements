"""When clear hold is met, long busy defers must run without waiting for busy_retry."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.date import DateTrigger

from app.scheduler import (
    _defer_id,
    nudge_manual_defer_jobs_if_clear_ready,
    run_manual_trigger,
)

TZ = ZoneInfo("America/New_York")


def test_nudge_pulls_long_busy_defer_when_clear_hold_met(monkeypatch):
    monkeypatch.setattr("app.scheduler.DRA_CLEAR_SECONDS", 10.0)

    nudged: list[str] = []

    class FakeRadio:
        def continuous_idle_seconds(self) -> float:
            return 12.0

    class FakeSched:
        def get_jobs(self):
            return [self.job]

        def add_job(self, func, trigger, *, id, replace_existing, kwargs, misfire_grace_time):
            nudged.append(id)
            assert func is run_manual_trigger
            assert kwargs.get("defer_kind") == "busy"

    run_at = datetime.now(TZ) + timedelta(seconds=30)
    FakeSched.job = type(
        "J",
        (),
        {
            "id": "defer:manual:sched1",
            "next_run_time": run_at,
            "kwargs": {
                "schedule_id": "sched1",
                "defer_kind": "busy",
            },
        },
    )()

    monkeypatch.setattr("app.scheduler.get_scheduler", lambda: FakeSched())
    monkeypatch.setattr("app.scheduler.get_radio", lambda: FakeRadio())

    nudge_manual_defer_jobs_if_clear_ready()
    assert nudged == ["defer:manual:sched1"]


def test_nudge_pulls_long_ptt_defer_when_clear_hold_met(monkeypatch):
    monkeypatch.setattr("app.scheduler.DRA_CLEAR_SECONDS", 10.0)

    nudged: list[str] = []

    class FakeRadio:
        def continuous_idle_seconds(self) -> float:
            return 12.0

    class FakeSched:
        def get_jobs(self):
            return [self.job]

        def add_job(self, func, trigger, *, id, replace_existing, kwargs, misfire_grace_time):
            nudged.append(id)

    run_at = datetime.now(TZ) + timedelta(seconds=30)
    FakeSched.job = type(
        "J",
        (),
        {
            "id": "defer:manual:sched1",
            "next_run_time": run_at,
            "kwargs": {"defer_kind": "ptt"},
        },
    )()

    monkeypatch.setattr("app.scheduler.get_scheduler", lambda: FakeSched())
    monkeypatch.setattr("app.scheduler.get_radio", lambda: FakeRadio())

    nudge_manual_defer_jobs_if_clear_ready()
    assert nudged == ["defer:manual:sched1"]


def test_nudge_leaves_short_ptt_retry_on_timer(monkeypatch):
    monkeypatch.setattr("app.scheduler.DRA_CLEAR_SECONDS", 10.0)

    class FakeRadio:
        def continuous_idle_seconds(self) -> float:
            return 12.0

    class FakeSched:
        def get_jobs(self):
            return [self.job]

        def add_job(self, *_args, **_kwargs):
            pytest.fail("1s PTT retry should not be nudged")

    run_at = datetime.now(TZ) + timedelta(seconds=1.0)
    FakeSched.job = type(
        "J",
        (),
        {
            "id": "defer:manual:sched1",
            "next_run_time": run_at,
            "kwargs": {"defer_kind": "ptt"},
        },
    )()

    monkeypatch.setattr("app.scheduler.get_scheduler", lambda: FakeSched())
    monkeypatch.setattr("app.scheduler.get_radio", lambda: FakeRadio())

    nudge_manual_defer_jobs_if_clear_ready()
