"""Thursday–Friday check window and the anchors inside it."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models import parse_hhmm


def window_for(
    now: datetime,
    start_hhmm: str,
    stop_hhmm: str,
) -> tuple[datetime, datetime] | None:
    """The open Thursday-start through Friday-stop window that contains ``now``."""
    if now.weekday() not in (3, 4):
        return None
    zone = now.tzinfo
    thursday = now.date() - timedelta(days=now.weekday() - 3)
    start_hour, start_minute = parse_hhmm(start_hhmm)
    stop_hour, stop_minute = parse_hhmm(stop_hhmm)
    start = datetime(
        thursday.year,
        thursday.month,
        thursday.day,
        start_hour,
        start_minute,
        tzinfo=zone,
    )
    friday = thursday + timedelta(days=1)
    stop = datetime(
        friday.year,
        friday.month,
        friday.day,
        stop_hour,
        stop_minute,
        tzinfo=zone,
    )
    if start <= now <= stop:
        return start, stop
    return None


def anchors(
    start: datetime,
    stop: datetime,
    *,
    interval_minutes: int = 60,
) -> list[datetime]:
    """Return check times from ``start`` through ``stop``, stepping by ``interval_minutes``."""
    if interval_minutes < 1:
        raise ValueError("interval must be at least one minute")
    found: list[datetime] = []
    moment = start
    step = timedelta(minutes=interval_minutes)
    while moment <= stop:
        found.append(moment)
        moment += step
    return found


def next_anchor_after(
    now: datetime,
    start: datetime,
    stop: datetime,
    *,
    interval_minutes: int = 60,
) -> datetime | None:
    for moment in anchors(start, stop, interval_minutes=interval_minutes):
        if moment > now:
            return moment
    return None


def success_in_window(completed_at: datetime | None, *, split_ok: bool, start: datetime, stop: datetime) -> bool:
    if not split_ok or completed_at is None:
        return False
    return start <= completed_at <= stop
