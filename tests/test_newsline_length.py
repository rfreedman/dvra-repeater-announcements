"""Spoken announcements that exceed the repeater timeout are not scheduled or played."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Announcement, Schedule, Settings, StoreDocument
from app.schedule_logic import resolve_slot, schedule_warning
from app.slots import slot_from_parts
from app.spoken_length import apply_played_length, recompute_too_long

ZONE = ZoneInfo("America/New_York")


def test_length_over_timeout_minus_two_is_too_long():
    item = Announcement(name="Long", text="Hello.")
    marked = apply_played_length(item, 178.1, 180)
    assert marked.too_long
    assert not apply_played_length(item, 178, 180).too_long
    assert not recompute_too_long(marked, 200).too_long


def test_too_long_schedule_is_replaced_by_the_baseline():
    short = Announcement(name="ID", text="id")
    long = apply_played_length(Announcement(name="Net", text="net"), 200, 180)
    baseline = Schedule(name="Baseline", kind="baseline", announcement_id=short.id)
    weekly = Schedule(
        name="Friday net",
        kind="weekly",
        announcement_id=long.id,
        days=["fri"],
        slots=["18:00"],
    )
    document = StoreDocument(
        settings=Settings(repeater_timeout_seconds=180),
        announcements=[short, long],
        schedules=[baseline, weekly],
    )
    slot = slot_from_parts(datetime(2026, 10, 23, tzinfo=ZONE).date(), "18:00", ZONE)
    assert slot is not None
    resolved = resolve_slot(document, slot)
    assert resolved.announcement is not None
    assert resolved.announcement.id == short.id
    assert resolved.skipped_detail
    assert "Friday net" in resolved.skipped_detail
    assert "ID will play instead" in resolved.skipped_detail
    warning = schedule_warning(weekly, document)
    assert warning is not None
    assert "over the" in warning


def test_schedule_api_rejects_a_new_too_long_choice(client, store):
    short = store.save_announcement(Announcement(name="ID", text="id"))
    long = store.save_announcement(
        apply_played_length(Announcement(name="Net", text="net"), 200, 180)
    )
    store.save_schedule(Schedule(name="Baseline", kind="baseline", announcement_id=short.id))
    created = client.post(
        "/api/schedules",
        json={"name": "Weekly", "kind": "weekly", "announcement_id": long.id, "days": ["fri"], "slots": ["18:00"]},
    )
    assert created.status_code == 400
    kept = store.save_schedule(
        Schedule(name="Friday net", kind="weekly", announcement_id=long.id, days=["fri"], slots=["18:00"])
    )
    listed = client.get("/api/schedules").json()
    assert any("Friday net" in note and "over the" in note for note in listed["warnings"])
    detail = client.get(f"/api/schedules/{kept.id}").json()
    assert "over the" in detail["warning"]
    same = client.put(
        f"/api/schedules/{kept.id}",
        json={"name": "Friday net", "kind": "weekly", "announcement_id": long.id, "days": ["fri"], "slots": ["18:00"]},
    )
    assert same.status_code == 200


def test_startup_times_only_announcements_without_a_stored_length(store, monkeypatch):
    from app.audio import PcmChunk
    from app.pcm_cache import warm_all

    timed = store.save_announcement(
        Announcement(name="Timed", text="Hi.", play_seconds=3.0, too_long=False)
    )
    untimed = store.save_announcement(Announcement(name="New", text="Hello there."))
    long_pcm = [PcmChunk(pcm_int16=b"\x00" * (22050 * 2 * 200), sample_rate=22050)]

    def fake(item):
        if item.id == untimed.id:
            return long_pcm
        return [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    monkeypatch.setattr("app.pcm_cache.warm_announcement", fake)
    warm_all()
    assert store.get(timed.id).play_seconds == 3.0
    measured = store.get(untimed.id)
    assert measured is not None
    assert measured.too_long
    assert measured.play_seconds is not None and measured.play_seconds > 178
