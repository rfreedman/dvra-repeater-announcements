"""sync_jobs must not re-arm fired slots, drop late overlays, or prune live busy retries."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import app.scheduler as scheduler_mod
from app.models import Announcement, Schedule

TZ = ZoneInfo("America/New_York")


class _Clock:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.astimezone(tz) if tz else _Clock.now


@pytest.fixture
def paused(monkeypatch, store):
    sched = BackgroundScheduler(timezone=TZ)
    sched.start(paused=True)
    monkeypatch.setattr(scheduler_mod, "_scheduler", sched)
    monkeypatch.setattr(scheduler_mod, "datetime", _FrozenDatetime)
    yield sched
    sched.shutdown(wait=False)


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=TZ)


def _sync_at(when: datetime) -> None:
    _Clock.now = when
    scheduler_mod.sync_jobs()


def _seed(store, *, overlay_offset: int | None = None):
    base = store.save_announcement(Announcement(name="ID", text="id"))
    store.save_schedule(Schedule(name="Base", kind="baseline", announcement_id=base.id))
    if overlay_offset is not None:
        net = store.save_announcement(Announcement(name="Net", text="net"))
        store.save_schedule(
            Schedule(
                name="Net",
                kind="weekly",
                days=["wed"],
                slots=["15:30"],
                offset_minutes=overlay_offset,
                announcement_id=net.id,
            )
        )


def test_fired_slot_is_not_rearmed_by_a_later_sync(paused, store):
    _seed(store)
    jid = "slot:2026-10-14T15:00"
    _sync_at(_at(14, 59))
    assert paused.get_job(jid) is not None
    paused.remove_job(jid)  # APScheduler removes a DateTrigger job once it runs
    _sync_at(_at(15, 0, 3))  # e.g. an admin saves something 3 s after the fire
    assert paused.get_job(jid) is None


def test_pending_past_slot_job_is_kept(paused, store):
    _seed(store)
    jid = "slot:2026-10-14T15:00"
    _sync_at(_at(14, 59))
    _sync_at(_at(15, 0, 10))  # scheduler woke late; job has not run yet
    assert paused.get_job(jid) is not None


def test_late_offset_overlay_survives_sync_after_slot_center(paused, store):
    _seed(store, overlay_offset=5)
    jid = "slot:2026-10-14T15:30"
    _sync_at(_at(15, 29))
    job = paused.get_job(jid)
    assert job is not None and job.next_run_time == _at(15, 35)
    _sync_at(_at(15, 32))
    job = paused.get_job(jid)
    assert job is not None and job.next_run_time == _at(15, 35)


def test_overlay_offset_moved_into_the_past_drops_stale_job(paused, store):
    _seed(store, overlay_offset=-5)
    jid = "slot:2026-10-14T15:30"
    _sync_at(_at(15, 20))
    job = paused.get_job(jid)
    assert job is not None and job.next_run_time == _at(15, 25)
    net = next(item for item in store.list_schedules() if item.kind == "weekly")
    store.save_schedule(net.model_copy(update={"offset_minutes": -10}))
    _sync_at(_at(15, 22))  # new fire_at 15:20 has passed; old job still said 15:25
    assert paused.get_job(jid) is None
    assert all(item.next_run_time != _at(15, 25) for item in paused.get_jobs())


def test_late_offset_overlay_is_scheduled_by_sync_after_slot_center(paused, store):
    _seed(store, overlay_offset=5)
    _sync_at(_at(15, 32))  # restart between slot center and fire time
    job = paused.get_job("slot:2026-10-14T15:30")
    assert job is not None and job.next_run_time == _at(15, 35)


def test_busy_retry_survives_until_its_own_deadline(paused, store):
    _seed(store)
    jid = "defer:2026-10-14T15:00"
    paused.add_job(print, "date", run_date=_at(15, 1, 30), id=jid)
    _sync_at(_at(15, 1, 0))  # well past the old 30 s cutoff
    assert paused.get_job(jid) is not None


def test_busy_retry_removed_when_slot_has_nothing_to_play(paused, store):
    _seed(store)
    jid = "defer:2026-10-14T15:00"
    paused.add_job(print, "date", run_date=_at(15, 0, 30), id=jid)
    baseline = next(item for item in store.list_schedules() if item.kind == "baseline")
    store.set_enabled(baseline.id, False)
    _sync_at(_at(15, 0, 10))
    assert paused.get_job(jid) is None


def test_slot_jobs_are_not_added_in_the_past(paused, store):
    _seed(store)
    _sync_at(_at(15, 0, 10))
    for job in paused.get_jobs():
        if job.id.startswith("slot:"):
            assert job.next_run_time > _at(15, 0, 10)
