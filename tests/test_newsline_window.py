"""Newsline check window, without touching the network."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.newsline.window import anchors, next_anchor_after, success_in_window, window_for

ZONE = ZoneInfo("America/New_York")


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-10-22 is a Thursday.
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


def test_window_is_open_from_thursday_start_through_friday_stop():
    assert window_for(_at(22, 11, 59), "12:00", "18:00") is None
    opened = window_for(_at(22, 12, 0), "12:00", "18:00")
    assert opened is not None
    assert opened[0] == _at(22, 12, 0)
    assert opened[1] == _at(23, 18, 0)
    assert window_for(_at(23, 18, 0), "12:00", "18:00") is not None
    assert window_for(_at(23, 18, 1), "12:00", "18:00") is None
    assert window_for(_at(25, 12, 0), "12:00", "18:00") is None


def test_anchors_are_hourly_from_the_thursday_start():
    start, stop = window_for(_at(22, 12, 30), "12:00", "18:00")
    found = anchors(start, stop)
    assert found[0] == _at(22, 12, 0)
    assert found[1] == _at(22, 13, 0)
    assert found[-1] <= stop
    assert next_anchor_after(_at(22, 12, 30), start, stop) == _at(22, 13, 0)
    assert next_anchor_after(stop, start, stop) is None


def test_success_inside_the_window_stops_the_checks():
    start, stop = window_for(_at(22, 15, 0), "12:00", "18:00")
    assert success_in_window(_at(22, 13, 0), split_ok=True, start=start, stop=stop)
    assert not success_in_window(_at(15, 13, 0), split_ok=True, start=start, stop=stop)
    assert not success_in_window(_at(22, 13, 0), split_ok=False, start=start, stop=stop)
