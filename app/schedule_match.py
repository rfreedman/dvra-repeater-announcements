"""Which overlay schedules occupy a half-hour slot."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.models import DAY_NAMES, Schedule
from app.slots import Slot, slot_from_parts


def nth_weekday_date(year: int, month: int, weekday: int, occurrence: int) -> date | None:
    days = [
        item
        for item in calendar.Calendar(firstweekday=0).itermonthdates(year, month)
        if item.month == month and item.weekday() == weekday
    ]
    if not days:
        return None
    index = occurrence - 1
    if index < 0 or index >= len(days):
        return None
    return days[index]


def overlay_matches(schedule: Schedule, slot: Slot) -> bool:
    """True if this overlay occupies `slot` (regardless of fire-time offset)."""
    if not schedule.enabled or not schedule.is_overlay:
        return False
    if schedule.kind == "emergency":
        return True
    stamp = slot.stamp
    if schedule.kind == "weekly":
        return slot.weekday in schedule.days and stamp in schedule.slots
    if schedule.kind == "monthly":
        if slot.day.month in set(schedule.skip_months):
            return False
        if stamp not in schedule.slots:
            return False
        weekday = DAY_NAMES.index(schedule.days[0])
        occurrence = schedule.occurrence if schedule.occurrence is not None else 1
        wanted = nth_weekday_date(slot.day.year, slot.day.month, weekday, occurrence)
        return slot.day == wanted
    if schedule.kind == "range":
        return _range_matches(schedule, slot)
    if schedule.kind == "monthly_range":
        return _monthly_range_matches(schedule, slot)
    return False


def _countdown_occupies(
    slot: Slot,
    start_day: date,
    event_day: date,
    event_stamp: str,
    allowed_stamps: list[str],
) -> bool:
    if slot.day < start_day:
        return False
    # Slots are always in the station zone (document settings), never Schedule.timezone.
    zone = slot.center.tzinfo
    if not isinstance(zone, ZoneInfo):
        return False
    event = slot_from_parts(event_day, event_stamp, zone)
    if event is None or slot.center >= event.center:
        return False
    if allowed_stamps and slot.stamp not in allowed_stamps:
        return False
    return True


def _range_matches(schedule: Schedule, slot: Slot) -> bool:
    if schedule.kind != "range":
        return False
    if schedule.start_date is None or schedule.event_date is None or not schedule.event_slot:
        return False
    return _countdown_occupies(
        slot,
        schedule.start_date,
        schedule.event_date,
        schedule.event_slot,
        schedule.slots,
    )


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return year, month


def _monthly_range_matches(schedule: Schedule, slot: Slot) -> bool:
    if schedule.kind != "monthly_range":
        return False
    if (
        not schedule.days
        or schedule.occurrence is None
        or not schedule.event_slot
        or schedule.days_before is None
    ):
        return False
    weekday = DAY_NAMES.index(schedule.days[0])
    skip = set(schedule.skip_months)
    months_ahead = max(1, (schedule.days_before // 28) + 2)
    year, month = slot.day.year, slot.day.month
    year, month = _shift_month(year, month, -1)
    for _ in range(months_ahead + 2):
        if month not in skip:
            event_day = nth_weekday_date(year, month, weekday, schedule.occurrence)
            if event_day is not None:
                start_day = event_day - timedelta(days=schedule.days_before)
                if _countdown_occupies(
                    slot,
                    start_day,
                    event_day,
                    schedule.event_slot,
                    schedule.slots,
                ):
                    return True
        year, month = _shift_month(year, month, 1)
    return False


def matching_overlays(schedules: list[Schedule], slot: Slot) -> list[Schedule]:
    return [item for item in schedules if overlay_matches(item, slot)]


def pick_overlay(schedules: list[Schedule], slot: Slot) -> tuple[Schedule | None, list[Schedule]]:
    """Return the winning overlay and any same-priority ties."""
    matches = matching_overlays(schedules, slot)
    if not matches:
        return None, []
    best = max(item.priority for item in matches)
    ties = [item for item in matches if item.priority == best]
    ties.sort(key=lambda item: item.id)
    return ties[0], ties
