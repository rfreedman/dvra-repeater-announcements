"""When clear hold is met, long busy defers must run without waiting for busy_retry."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from collections.abc import Callable
from zoneinfo import ZoneInfo

import pytest

from app.manual_trigger_state import nudge_manual_defer_jobs_if_clear_ready
from app.scheduler import run_manual_trigger

TZ = ZoneInfo("America/New_York")


@dataclass
class _FakeJob:
    id: str
    next_run_time: datetime
    kwargs: dict[str, str]


def test_nudge_pulls_long_busy_defer_when_clear_hold_met(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.manual_trigger_state.DRA_CLEAR_SECONDS", 10.0)

    nudged: list[str] = []

    class FakeRadio:
        def continuous_idle_seconds(self) -> float:
            return 12.0

    class FakeSched:
        job: _FakeJob = _FakeJob("", datetime.min.replace(tzinfo=TZ), {})

        def get_jobs(self) -> list[_FakeJob]:
            return [self.job]

        def add_job(
            self,
            func: Callable[..., object],
            trigger: object,
            *,
            id: str,
            replace_existing: bool,
            kwargs: dict[str, str],
            misfire_grace_time: int,
        ) -> None:
            _ = trigger
            _ = replace_existing
            _ = misfire_grace_time
            nudged.append(id)
            assert func is run_manual_trigger
            assert kwargs.get("defer_kind") == "busy"

    run_at = datetime.now(TZ) + timedelta(seconds=30)
    FakeSched.job = _FakeJob(
        id="defer:manual:sched1",
        next_run_time=run_at,
        kwargs={"schedule_id": "sched1", "defer_kind": "busy"},
    )

    def get_scheduler() -> FakeSched:
        return FakeSched()

    def get_radio() -> FakeRadio:
        return FakeRadio()

    monkeypatch.setattr("app.manual_trigger_state.get_scheduler", get_scheduler)
    monkeypatch.setattr("app.manual_trigger_state.get_radio", get_radio)

    nudge_manual_defer_jobs_if_clear_ready()
    assert nudged == ["defer:manual:sched1"]


def test_nudge_pulls_long_ptt_defer_when_clear_hold_met(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.manual_trigger_state.DRA_CLEAR_SECONDS", 10.0)

    nudged: list[str] = []

    class FakeRadio:
        def continuous_idle_seconds(self) -> float:
            return 12.0

    class FakeSched:
        job: _FakeJob = _FakeJob("", datetime.min.replace(tzinfo=TZ), {})

        def get_jobs(self) -> list[_FakeJob]:
            return [self.job]

        def add_job(
            self,
            func: Callable[..., object],
            trigger: object,
            *,
            id: str,
            replace_existing: bool,
            kwargs: dict[str, str],
            misfire_grace_time: int,
        ) -> None:
            _ = func
            _ = trigger
            _ = replace_existing
            _ = kwargs
            _ = misfire_grace_time
            nudged.append(id)

    run_at = datetime.now(TZ) + timedelta(seconds=30)
    FakeSched.job = _FakeJob(
        id="defer:manual:sched1",
        next_run_time=run_at,
        kwargs={"defer_kind": "ptt"},
    )

    def get_scheduler() -> FakeSched:
        return FakeSched()

    def get_radio() -> FakeRadio:
        return FakeRadio()

    monkeypatch.setattr("app.manual_trigger_state.get_scheduler", get_scheduler)
    monkeypatch.setattr("app.manual_trigger_state.get_radio", get_radio)

    nudge_manual_defer_jobs_if_clear_ready()
    assert nudged == ["defer:manual:sched1"]


def test_nudge_leaves_short_ptt_retry_on_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.manual_trigger_state.DRA_CLEAR_SECONDS", 10.0)

    class FakeRadio:
        def continuous_idle_seconds(self) -> float:
            return 12.0

    class FakeSched:
        job: _FakeJob = _FakeJob("", datetime.min.replace(tzinfo=TZ), {})

        def get_jobs(self) -> list[_FakeJob]:
            return [self.job]

        def add_job(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("1s PTT retry should not be nudged")

    run_at = datetime.now(TZ) + timedelta(seconds=1.0)
    FakeSched.job = _FakeJob(
        id="defer:manual:sched1",
        next_run_time=run_at,
        kwargs={"defer_kind": "ptt"},
    )

    def get_scheduler() -> FakeSched:
        return FakeSched()

    def get_radio() -> FakeRadio:
        return FakeRadio()

    monkeypatch.setattr("app.manual_trigger_state.get_scheduler", get_scheduler)
    monkeypatch.setattr("app.manual_trigger_state.get_radio", get_radio)

    nudge_manual_defer_jobs_if_clear_ready()
