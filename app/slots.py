"""Half-hour clock slots.

A slot is identified by its center time (:00 or :30), not by the moment
audio actually goes out. An overlay may fire a few minutes early or late
inside the configured window; it still occupies that slot, so nothing else
plays at the center.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import DAY_NAMES, format_hhmm, parse_hhmm

SLOT_MINUTES = (0, 30)


@dataclass(frozen=True)
class Slot:
    """One :00 or :30 on a calendar day, in the station timezone."""

    center: datetime

    @property
    def stamp(self) -> str:
        return format_hhmm(self.center.hour, self.center.minute)

    @property
    def day(self) -> date:
        return self.center.date()

    @property
    def weekday(self) -> str:
        return DAY_NAMES[self.center.weekday()]

    @property
    def key(self) -> str:
        return self.center.strftime("%Y-%m-%dT%H:%M")

    def fire_at(self, offset_minutes: int) -> datetime:
        return self.center + timedelta(minutes=offset_minutes)


def zone_for(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def as_local(when: datetime, zone: ZoneInfo) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=zone)
    return when.astimezone(zone)


def slot_from_parts(day: date, stamp: str, zone: ZoneInfo) -> Slot | None:
    hour, minute = parse_hhmm(stamp)
    if minute not in SLOT_MINUTES:
        raise ValueError(f"Not a slot time: {stamp}")
    center = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    probe = center.astimezone(timezone.utc).astimezone(zone)
    if probe.hour != hour or probe.minute != minute:
        return None
    return Slot(center=center.replace(second=0, microsecond=0))


def slot_from_key(key: str, zone: ZoneInfo) -> Slot:
    local = datetime.fromisoformat(key)
    if local.tzinfo is None:
        local = local.replace(tzinfo=zone)
    else:
        local = local.astimezone(zone)
    found = slot_from_parts(local.date(), format_hhmm(local.hour, local.minute), zone)
    if found is None:
        raise ValueError(f"Invalid slot {key}")
    return found


def iter_day_slots(day: date, zone: ZoneInfo):
    for hour in range(24):
        for minute in SLOT_MINUTES:
            found = slot_from_parts(day, format_hhmm(hour, minute), zone)
            if found is not None:
                yield found


def iter_slots(after: datetime, until: datetime, zone: ZoneInfo):
    """Slot centers in (after, until)."""
    local_after = as_local(after, zone)
    local_until = as_local(until, zone)
    day = local_after.date()
    last_day = local_until.date()
    while day <= last_day + timedelta(days=1):
        for slot in iter_day_slots(day, zone):
            if slot.center > local_after and slot.center < local_until:
                yield slot
        day += timedelta(days=1)
