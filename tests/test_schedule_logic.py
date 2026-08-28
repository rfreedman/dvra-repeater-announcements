from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Announcement, Exclusion, Schedule
from app.schedule_logic import is_excluded, next_run_at, summarize, trigger_warning

TZ = ZoneInfo("America/New_York")


def hourly(**kwargs) -> Schedule:
    data = {"kind": "hourly", "minute": 55, "timezone": "America/New_York"}
    data.update(kwargs)
    return Schedule.model_validate(data)


def test_hourly_summary_with_sunday_time_exclusion():
    schedule = hourly(
        exclusions=[Exclusion(kind="day_time", days=["sun"], start="21:00", end="22:00")],
    )
    assert summarize(schedule) == "Every hour at :55\nexcept Sunday 21:00–22:00"


def test_exclusion_note_in_summary():
    schedule = hourly(
        exclusions=[Exclusion(kind="day", days=["sun"], note="  club net  ")],
    )
    assert schedule.exclusions[0].note == "club net"
    assert summarize(schedule) == "Every hour at :55\nexcept Sundays (club net)"


def test_multiple_exclusions_are_separate_lines():
    schedule = hourly(
        exclusions=[
            Exclusion(kind="day", days=["sun"], note="club net"),
            Exclusion(kind="day_time", days=["mon"], start="21:00", end="22:00", note="meeting"),
        ],
    )
    assert summarize(schedule) == (
        "Every hour at :55\nexcept Sundays (club net)\nexcept Monday 21:00–22:00 (meeting)"
    )


def test_daily_summary_skip_sundays():
    schedule = Schedule.model_validate(
        {
            "kind": "daily",
            "times": ["09:00", "21:55"],
            "timezone": "America/New_York",
            "exclusions": [{"kind": "day", "days": ["sun"]}],
        }
    )
    assert summarize(schedule) == "09:00 and 21:55 every day\nexcept Sundays"


def test_skip_sunday_2155_only():
    schedule = hourly(
        exclusions=[Exclusion(kind="day_time", days=["sun"], start="21:55", end="21:55")]
    )
    sunday_2155 = datetime(2026, 8, 30, 21, 55, tzinfo=TZ)
    sunday_2055 = datetime(2026, 8, 30, 20, 55, tzinfo=TZ)
    monday_2155 = datetime(2026, 8, 31, 21, 55, tzinfo=TZ)
    assert is_excluded(schedule, sunday_2155)
    assert not is_excluded(schedule, sunday_2055)
    assert not is_excluded(schedule, monday_2155)


def test_next_run_skips_excluded_slot():
    schedule = hourly(
        exclusions=[Exclusion(kind="day_time", days=["sun"], start="21:00", end="22:00")]
    )
    after = datetime(2026, 8, 30, 21, 54, tzinfo=TZ)
    nxt = next_run_at(schedule, after)
    assert nxt == datetime(2026, 8, 30, 22, 55, tzinfo=TZ)


def test_daily_skip_day_moves_to_monday():
    schedule = Schedule.model_validate(
        {
            "kind": "daily",
            "times": ["09:00"],
            "timezone": "America/New_York",
            "exclusions": [{"kind": "day", "days": ["sun"]}],
        }
    )
    sunday = datetime(2026, 8, 30, 8, 0, tzinfo=TZ)
    nxt = next_run_at(schedule, sunday)
    assert nxt == datetime(2026, 8, 31, 9, 0, tzinfo=TZ)


def test_disabled_has_no_next_run():
    schedule = hourly(enabled=False)
    assert next_run_at(schedule, datetime(2026, 8, 27, 12, 0, tzinfo=TZ)) is None


def test_hourly_multiple_minutes():
    schedule = Schedule.model_validate(
        {"kind": "hourly", "minutes": [0, 30], "timezone": "America/New_York"}
    )
    assert summarize(schedule) == "Every hour at :00 and :30"
    after = datetime(2026, 8, 27, 12, 7, tzinfo=TZ)
    assert next_run_at(schedule, after) == datetime(2026, 8, 27, 12, 30, tzinfo=TZ)


def test_weekly_summary_and_next_run_skips_other_days():
    schedule = Schedule.model_validate(
        {
            "kind": "weekly",
            "days": ["wed", "mon"],
            "times": ["21:00", "19:00"],
            "timezone": "America/New_York",
        }
    )
    assert schedule.days == ["mon", "wed"]
    assert schedule.times == ["19:00", "21:00"]
    assert summarize(schedule) == "19:00 and 21:00 on Mondays and Wednesdays"
    sunday = datetime(2026, 8, 30, 20, 0, tzinfo=TZ)
    assert next_run_at(schedule, sunday) == datetime(2026, 8, 31, 19, 0, tzinfo=TZ)
    after_monday_net = datetime(2026, 8, 31, 19, 30, tzinfo=TZ)
    assert next_run_at(schedule, after_monday_net) == datetime(2026, 8, 31, 21, 0, tzinfo=TZ)
    after_wednesday = datetime(2026, 9, 2, 21, 1, tzinfo=TZ)
    assert next_run_at(schedule, after_wednesday) == datetime(2026, 9, 7, 19, 0, tzinfo=TZ)


def test_weekly_exclusion_still_applies():
    schedule = Schedule.model_validate(
        {
            "kind": "weekly",
            "days": ["sun"],
            "times": ["19:00", "21:00"],
            "timezone": "America/New_York",
            "exclusions": [{"kind": "day_time", "days": ["sun"], "start": "19:00", "end": "19:00"}],
        }
    )
    sunday = datetime(2026, 8, 30, 18, 0, tzinfo=TZ)
    nxt = next_run_at(schedule, sunday)
    assert nxt == datetime(2026, 8, 30, 21, 0, tzinfo=TZ)


def test_weekly_requires_days_and_times():
    import pytest

    with pytest.raises(Exception):
        Schedule.model_validate({"kind": "weekly", "times": ["19:00"], "timezone": "America/New_York"})
    with pytest.raises(Exception):
        Schedule.model_validate({"kind": "weekly", "days": ["mon"], "timezone": "America/New_York"})


def test_schedules_conflict_on_shared_slot():
    from app.schedule_logic import find_conflicts

    one = hourly()
    other = Schedule.model_validate(
        {"kind": "daily", "times": ["12:55"], "timezone": "America/New_York"}
    )
    hits = find_conflicts(one, [("Net", other)], now=datetime(2026, 8, 27, 12, 0, tzinfo=TZ))
    assert hits
    none = Schedule.model_validate(
        {"kind": "hourly", "minutes": [0], "timezone": "America/New_York"}
    )
    assert find_conflicts(one, [("Other", none)], now=datetime(2026, 8, 27, 12, 0, tzinfo=TZ)) == []


def test_unscheduled_announcement_has_warning():
    item = Announcement(name="Tech Net", text="Stand by", schedules=[])
    assert trigger_warning(item, None) == "Won't play until a schedule is saved."


def test_empty_text_has_warning():
    item = Announcement(name="Tech Net", text="  ", schedules=[])
    assert trigger_warning(item, None) == "Won't play — missing announcement text and a schedule."


def test_past_once_has_warning():
    item = Announcement(name="Once", text="Hello")
    schedule = Schedule.model_validate(
        {
            "kind": "once",
            "at": datetime(2020, 1, 1, 12, 0, tzinfo=TZ),
            "timezone": "America/New_York",
        }
    )
    assert trigger_warning(item, schedule, now=datetime(2026, 8, 27, 12, 0, tzinfo=TZ)) == (
        "Won't play — the date and time have already passed."
    )
