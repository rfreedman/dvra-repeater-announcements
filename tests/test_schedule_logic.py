from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.models import Announcement, Schedule, Settings, StoreDocument
from app.schedule_logic import (
    find_conflicts,
    next_rotation,
    overlay_matches,
    resolve_slot,
    resolve_window,
    summarize,
)
from app.slots import Slot, slot_from_parts, zone_for

TZ = ZoneInfo("America/New_York")
ZONE = zone_for("America/New_York")


def _ann(name: str = "Station ID", text: str = "This is w2-zee-q.", **kwargs) -> Announcement:
    return Announcement(name=name, text=text, **kwargs)


def _baseline(announcement_id: str, name: str = "Baseline ID", **kwargs) -> Schedule:
    return Schedule(name=name, kind="baseline", announcement_id=announcement_id, **kwargs)


def _weekly(**kwargs) -> Schedule:
    data = {
        "name": "Weekly",
        "kind": "weekly",
        "days": ["sun"],
        "slots": ["21:00"],
        "offset_minutes": -5,
        **kwargs,
    }
    return Schedule.model_validate(data)


def _doc(*announcements: Announcement, schedules: list[Schedule] | None = None) -> StoreDocument:
    return StoreDocument(
        settings=Settings(timezone="America/New_York"),
        announcements=list(announcements),
        schedules=list(schedules or []),
    )


def _slot(day: date, stamp: str) -> Slot:
    found = slot_from_parts(day, stamp, ZONE)
    assert found is not None
    return found


def test_one_baseline_fills_every_slot():
    ann = _ann(id="a1")
    base = _baseline("a1", id="s1")
    document = _doc(ann, schedules=[base])
    noon = _slot(date(2026, 9, 1), "12:00")
    half = _slot(date(2026, 9, 1), "12:30")
    assert resolve_slot(document, noon).announcement.id == "a1"
    assert resolve_slot(document, half).fire_at == half.center
    assert resolve_slot(document, noon).source == "baseline"


def test_two_baselines_rotate_without_repeat():
    a = _ann(id="a1", name="ID")
    b = _ann(id="a2", name="Club")
    document = _doc(
        a,
        b,
        schedules=[_baseline("a1", id="s1", name="ID"), _baseline("a2", id="s2", name="Club")],
    )
    after = datetime(2026, 9, 1, 11, 59, tzinfo=TZ)
    until = datetime(2026, 9, 1, 14, 0, tzinfo=TZ)
    names = [item.announcement.name for item in resolve_window(document, after, until)]
    assert names[:4] == ["ID", "Club", "ID", "Club"]


def test_disabled_baseline_is_skipped():
    a = _ann(id="a1", name="ID")
    b = _ann(id="a2", name="Club")
    document = _doc(
        a,
        b,
        schedules=[
            _baseline("a1", id="s1", name="ID", enabled=False),
            _baseline("a2", id="s2", name="Club"),
        ],
    )
    slot = _slot(date(2026, 9, 1), "12:00")
    assert resolve_slot(document, slot).announcement.name == "Club"


def test_weekly_net_shifts_minus_five_and_skips_slot_center():
    ann = _ann(id="a1")
    net = _ann(id="a2", name="Tech Net")
    document = _doc(
        ann,
        net,
        schedules=[
            _baseline("a1", id="s1"),
            _weekly(id="s2", announcement_id="a2"),
        ],
    )
    sunday = date(2026, 9, 6)
    slot = _slot(sunday, "21:00")
    resolved = resolve_slot(document, slot)
    assert resolved.source == "overlay"
    assert resolved.announcement.name == "Tech Net"
    assert resolved.fire_at == datetime(2026, 9, 6, 20, 55, tzinfo=TZ)
    earlier = resolve_slot(document, _slot(sunday, "20:30"))
    assert earlier.source == "baseline"
    assert earlier.fire_at == datetime(2026, 9, 6, 20, 30, tzinfo=TZ)


def test_monthly_first_wednesday_skips_august():
    net = _ann(id="a2", name="PepperNet")
    document = _doc(
        _ann(id="a1"),
        net,
        schedules=[
            _baseline("a1", id="s1"),
            Schedule.model_validate(
                {
                    "id": "s2",
                    "name": "Monthly",
                    "kind": "monthly",
                    "announcement_id": "a2",
                    "days": ["wed"],
                    "occurrence": 1,
                    "slots": ["19:00"],
                    "offset_minutes": -5,
                    "skip_months": [8],
                }
            ),
        ],
    )
    first_wed_sep = _slot(date(2026, 9, 2), "19:00")
    first_wed_aug = _slot(date(2026, 8, 5), "19:00")
    assert overlay_matches(document.schedules[1], first_wed_sep)
    assert not overlay_matches(document.schedules[1], first_wed_aug)
    assert resolve_slot(document, first_wed_sep).fire_at.minute == 55
    assert resolve_slot(document, first_wed_aug).source == "baseline"


def test_range_fills_until_event_slot_not_after():
    promo = _ann(id="a2", name="Meeting promo")
    overlay = Schedule.model_validate(
        {
            "id": "s2",
            "name": "September meeting",
            "kind": "range",
            "announcement_id": "a2",
            "start_date": "2026-09-01",
            "event_date": "2026-09-02",
            "event_slot": "19:00",
        }
    )
    document = _doc(_ann(id="a1"), promo, schedules=[_baseline("a1", id="s1"), overlay])
    assert resolve_slot(document, _slot(date(2026, 9, 1), "00:00")).announcement.name == "Meeting promo"
    assert resolve_slot(document, _slot(date(2026, 9, 2), "18:30")).announcement.name == "Meeting promo"
    event = resolve_slot(document, _slot(date(2026, 9, 2), "19:00"))
    after = resolve_slot(document, _slot(date(2026, 9, 2), "19:30"))
    next_day = resolve_slot(document, _slot(date(2026, 9, 3), "00:00"))
    assert event.source == "baseline"
    assert after.source == "baseline"
    assert next_day.source == "baseline"


def test_monthly_range_starts_n_days_before_each_month():
    promo = _ann(id="a2", name="Meeting promo")
    overlay = Schedule.model_validate(
        {
            "id": "s2",
            "name": "Monthly countdown",
            "kind": "monthly_range",
            "announcement_id": "a2",
            "days": ["wed"],
            "occurrence": 1,
            "event_slot": "19:00",
            "days_before": 7,
        }
    )
    document = _doc(_ann(id="a1"), promo, schedules=[_baseline("a1", id="s1"), overlay])
    # First Wednesday of September 2026 is the 2nd; 7 days before is August 26.
    assert resolve_slot(document, _slot(date(2026, 8, 26), "00:00")).announcement.name == "Meeting promo"
    assert resolve_slot(document, _slot(date(2026, 8, 25), "23:30")).source == "baseline"
    assert resolve_slot(document, _slot(date(2026, 9, 2), "18:30")).announcement.name == "Meeting promo"
    assert resolve_slot(document, _slot(date(2026, 9, 2), "19:00")).source == "baseline"
    assert resolve_slot(document, _slot(date(2026, 9, 2), "19:30")).source == "baseline"
    assert "7 days before" in summarize(overlay)


def test_monthly_range_skip_month_leaves_baseline():
    overlay = Schedule.model_validate(
        {
            "name": "Monthly countdown",
            "kind": "monthly_range",
            "announcement_id": "a2",
            "days": ["wed"],
            "occurrence": 1,
            "event_slot": "19:00",
            "days_before": 7,
            "skip_months": [8],
        }
    )
    document = _doc(_ann(id="a1"), _ann(id="a2", name="Promo"), schedules=[_baseline("a1", id="s1"), overlay])
    # First Wednesday of August 2026 is the 5th; skipped, so no August window.
    assert resolve_slot(document, _slot(date(2026, 8, 4), "12:00")).source == "baseline"
    assert resolve_slot(document, _slot(date(2026, 8, 5), "18:30")).source == "baseline"


def test_weekly_beats_range_on_the_net_slot():
    document = _doc(
        _ann(id="a1"),
        _ann(id="a2", name="Promo"),
        _ann(id="a3", name="Tech Net"),
        schedules=[
            _baseline("a1", id="s1"),
            Schedule.model_validate(
                {
                    "id": "range1",
                    "name": "Promo week",
                    "kind": "range",
                    "announcement_id": "a2",
                    "start_date": "2026-09-01",
                    "event_date": "2026-09-07",
                    "event_slot": "12:00",
                }
            ),
            _weekly(id="net1", announcement_id="a3"),
        ],
    )
    sunday_net = resolve_slot(document, _slot(date(2026, 9, 6), "21:00"))
    sunday_other = resolve_slot(document, _slot(date(2026, 9, 6), "20:00"))
    assert sunday_net.announcement.name == "Tech Net"
    assert sunday_other.announcement.name == "Promo"


def test_equal_priority_is_a_conflict():
    left = _weekly(id="n1", announcement_id="a2", name="Net A")
    right = _weekly(id="n2", announcement_id="a3", name="Net B")
    document = _doc(
        _ann(id="a2", name="A"),
        _ann(id="a3", name="B"),
        schedules=[left],
    )
    hits = find_conflicts(right, document, skip_id="n2", now=datetime(2026, 9, 1, 12, 0, tzinfo=TZ))
    assert hits
    assert hits[0]["name"] == "Net A"
    assert hits[0]["slot"] == "21:00"


def test_silence_occupies_slot_without_announcement():
    silence = _weekly(id="quiet", announcement_id=None, name="Net in progress", slots=["21:30"])
    document = _doc(_ann(id="a1"), schedules=[_baseline("a1", id="s1"), silence])
    resolved = resolve_slot(document, _slot(date(2026, 9, 6), "21:30"))
    assert resolved.source == "silence"
    assert resolved.announcement is None
    assert "silence" in summarize(silence)


def test_emergency_wins_every_slot_until_cleared():
    emergency = Schedule.model_validate(
        {
            "id": "e1",
            "name": "Emergency",
            "kind": "emergency",
            "announcement_id": "a9",
        }
    )
    document = _doc(
        _ann(id="a1"),
        _ann(id="a9", name="Skywarn"),
        schedules=[_baseline("a1", id="s1"), _weekly(id="n1", announcement_id="a1"), emergency],
    )
    net_slot = resolve_slot(document, _slot(date(2026, 9, 6), "21:00"))
    random_slot = resolve_slot(document, _slot(date(2026, 9, 1), "03:30"))
    assert net_slot.source == "emergency"
    assert net_slot.announcement.name == "Skywarn"
    assert random_slot.source == "emergency"


def test_rotation_does_not_advance_twice_for_the_same_slot():
    document = _doc(
        _ann(id="a1"),
        _ann(id="a2", name="Club"),
        schedules=[_baseline("a1", id="s1"), _baseline("a2", id="s2")],
    )
    order, index, key = next_rotation(document, "2026-09-01T12:00")
    again = document.model_copy(
        update={"rotation": document.rotation.model_copy(update={"order": order, "index": index, "last_consumed_slot": key})}
    )
    order2, index2, _key = next_rotation(again, "2026-09-01T12:00")
    assert (order2, index2) == (order, index)


def test_offset_example_is_fifteen_twenty_five_not_fifteen_ten():
    overlay = Schedule.model_validate(
        {
            "name": "Shifted",
            "kind": "once",
            "announcement_id": "a2",
            "on_date": "2026-09-01",
            "slots": ["15:30"],
            "offset_minutes": -5,
        }
    )
    document = _doc(_ann(id="a1"), _ann(id="a2"), schedules=[_baseline("a1", id="s1"), overlay])
    resolved = resolve_slot(document, _slot(date(2026, 9, 1), "15:30"))
    assert resolved.fire_at == datetime(2026, 9, 1, 15, 25, tzinfo=TZ)
    neighbor = resolve_slot(document, _slot(date(2026, 9, 1), "15:00"))
    assert neighbor.source == "baseline"
    assert neighbor.fire_at == datetime(2026, 9, 1, 15, 0, tzinfo=TZ)


def test_baseline_summary():
    assert summarize(_baseline("a1")) == "Baseline — every hour and half-hour"


def test_weekly_summary_mentions_early():
    text = summarize(_weekly())
    assert "21:00" in text
    assert "Sundays" in text
    assert "5 minutes early" in text


def test_weekly_summary_collapses_contiguous_slots_and_days():
    overlay = _weekly(
        announcement_id="a2",
        days=["mon", "tue", "wed"],
        slots=["20:00", "20:30", "21:00"],
        offset_minutes=0,
    )
    assert summarize(overlay) == "the 20:00–21:00 slots on Monday–Wednesday"


def test_weekly_summary_every_day_and_split_slot_ranges():
    overlay = _weekly(
        announcement_id="a2",
        days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        slots=["08:00", "08:30", "21:00"],
        offset_minutes=0,
    )
    assert summarize(overlay) == "the 08:00–08:30 slots and the 21:00 slot every day"


def test_weekly_summary_wraps_weekend_into_a_day_range():
    overlay = _weekly(announcement_id="a2", days=["sat", "sun", "mon"], slots=["09:00"], offset_minutes=0)
    assert summarize(overlay) == "the 09:00 slot on Saturday–Monday"


def test_slot_phrase_wraps_midnight():
    overlay = _weekly(
        announcement_id="a2",
        days=["fri"],
        slots=["23:00", "23:30", "00:00", "00:30"],
        offset_minutes=0,
    )
    assert summarize(overlay) == "the 23:00–00:30 slots on Fridays"
