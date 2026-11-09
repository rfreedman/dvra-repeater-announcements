"""sync_jobs must not re-arm fired slots, drop late overlays, or prune live busy retries."""
from __future__ import annotations

from datetime import datetime, tzinfo as TzInfo
from collections.abc import Iterator
from typing import Protocol, cast, override
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import app.scheduler as scheduler_mod
import app.scheduler_jobs as scheduler_jobs
from app.scheduler_instance import get_scheduler, set_scheduler
from app.models import BaselineSchedule, TtsAnnouncement, parse_schedule

from app.store import AnnouncementStore
TZ = ZoneInfo("America/New_York")


class _ScheduledJob(Protocol):
    id: str
    next_run_time: datetime


def _job(scheduler: BackgroundScheduler, job_id: str) -> _ScheduledJob | None:
    return cast(_ScheduledJob | None, scheduler.get_job(job_id))


def _jobs(scheduler: BackgroundScheduler) -> list[_ScheduledJob]:
    return cast(list[_ScheduledJob], scheduler.get_jobs())


class _Clock:
    now: datetime = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)


class _FrozenDatetime(datetime):
    @classmethod
    @override
    def now(cls, tz: TzInfo | None = None) -> datetime:
        return _Clock.now.astimezone(tz) if tz else _Clock.now


@pytest.fixture
def paused(monkeypatch: pytest.MonkeyPatch, store: AnnouncementStore) -> Iterator[BackgroundScheduler]:
    _ = store
    sched = BackgroundScheduler(timezone=TZ)
    sched.start(paused=True)
    previous = get_scheduler()
    set_scheduler(sched)
    monkeypatch.setattr(scheduler_jobs, "datetime", _FrozenDatetime)
    yield sched
    set_scheduler(previous)
    sched.shutdown(wait=False)


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=TZ)


def _sync_at(when: datetime) -> None:
    _Clock.now = when
    scheduler_mod.sync_jobs()


def _seed(store: AnnouncementStore, *, overlay_offset: int | None = None) -> None:
    base = store.save_announcement(TtsAnnouncement(name="ID", text="id"))
    _ = store.save_schedule(BaselineSchedule(name="Base", announcement_id=base.id))
    if overlay_offset is not None:
        net = store.save_announcement(TtsAnnouncement(name="Net", text="net"))
        _ = store.save_schedule(
            parse_schedule(
                {
                    "name": "Net",
                    "kind": "weekly",
                    "days": ["wed"],
                    "slots": ["15:30"],
                    "offset_minutes": overlay_offset,
                    "announcement_id": net.id,
                }
            )
        )


def test_fired_slot_is_not_rearmed_by_a_later_sync(paused: BackgroundScheduler, store: AnnouncementStore) -> None:
    _seed(store)
    jid = "slot:2026-10-14T15:00"
    _sync_at(_at(14, 59))
    assert _job(paused, jid) is not None
    paused.remove_job(jid)  # APScheduler removes a DateTrigger job once it runs
    _sync_at(_at(15, 0, 3))  # e.g. an admin saves something 3 s after the fire
    assert _job(paused, jid) is None


def test_pending_past_slot_job_is_kept(paused: BackgroundScheduler, store: AnnouncementStore) -> None:
    _seed(store)
    jid = "slot:2026-10-14T15:00"
    _sync_at(_at(14, 59))
    _sync_at(_at(15, 0, 10))  # scheduler woke late; job has not run yet
    assert _job(paused, jid) is not None


def test_late_offset_overlay_survives_sync_after_slot_center(paused: BackgroundScheduler, store: AnnouncementStore) -> None:
    _seed(store, overlay_offset=5)
    jid = "slot:2026-10-14T15:30"
    _sync_at(_at(15, 29))
    job = _job(paused, jid)
    assert job is not None and job.next_run_time == _at(15, 35)
    _sync_at(_at(15, 32))
    job = _job(paused, jid)
    assert job is not None and job.next_run_time == _at(15, 35)


def test_overlay_offset_moved_into_the_past_drops_stale_job(paused: BackgroundScheduler, store: AnnouncementStore) -> None:
    _seed(store, overlay_offset=-5)
    jid = "slot:2026-10-14T15:30"
    _sync_at(_at(15, 20))
    job = _job(paused, jid)
    assert job is not None and job.next_run_time == _at(15, 25)
    net = next(item for item in store.list_schedules() if item.kind == "weekly")
    _ = store.save_schedule(net.model_copy(update={"offset_minutes": -10}))
    _sync_at(_at(15, 22))  # new fire_at 15:20 has passed; old job still said 15:25
    assert _job(paused, jid) is None
    assert all(item.next_run_time != _at(15, 25) for item in _jobs(paused))


def test_late_offset_overlay_is_scheduled_by_sync_after_slot_center(paused: BackgroundScheduler, store: AnnouncementStore) -> None:
    _seed(store, overlay_offset=5)
    _sync_at(_at(15, 32))  # restart between slot center and fire time
    job = _job(paused, "slot:2026-10-14T15:30")
    assert job is not None and job.next_run_time == _at(15, 35)


def test_busy_retry_survives_until_its_own_deadline(paused: BackgroundScheduler, store: AnnouncementStore) -> None:
    _seed(store)
    jid = "defer:2026-10-14T15:00"
    _ = paused.add_job(print, "date", run_date=_at(15, 1, 30), id=jid)
    _sync_at(_at(15, 1, 0))  # well past the old 30 s cutoff
    assert _job(paused, jid) is not None


def test_busy_retry_removed_when_slot_has_nothing_to_play(paused: BackgroundScheduler, store: AnnouncementStore) -> None:
    _seed(store)
    jid = "defer:2026-10-14T15:00"
    _ = paused.add_job(print, "date", run_date=_at(15, 0, 30), id=jid)
    baseline = next(item for item in store.list_schedules() if item.kind == "baseline")
    _ = store.set_enabled(baseline.id, False)
    _sync_at(_at(15, 0, 10))
    assert _job(paused, jid) is None


def test_slot_jobs_are_not_added_in_the_past(paused: BackgroundScheduler, store: AnnouncementStore) -> None:
    _seed(store)
    _sync_at(_at(15, 0, 10))
    for job in _jobs(paused):
        if job.id.startswith("slot:"):
            assert job.next_run_time > _at(15, 0, 10)


def test_second_sync_skips_unchanged_slot_jobs(paused: BackgroundScheduler, store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _ = monkeypatch
    _seed(store)
    _sync_at(_at(14, 0))
    with patch.object(paused, "add_job", wraps=paused.add_job) as tracking_add:
        with caplog.at_level("INFO", logger="app.scheduler"):
            _sync_at(_at(14, 0))
    tracking_add.assert_not_called()
    assert not any("Synced slot jobs" in record.message for record in caplog.records)


def test_sync_logs_one_summary_when_jobs_change(paused: BackgroundScheduler, store: AnnouncementStore, caplog: pytest.LogCaptureFixture) -> None:
    _ = paused
    _seed(store)
    with caplog.at_level("INFO", logger="app.scheduler"):
        _sync_at(_at(14, 0))
    summaries = [r.message for r in caplog.records if "Synced slot jobs" in r.message]
    assert len(summaries) == 1
    assert "added " in summaries[0]
    assert "removed 0" in summaries[0]
