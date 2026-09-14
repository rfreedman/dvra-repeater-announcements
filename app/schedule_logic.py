"""Resolve which announcement owns each half-hour slot.

Every :00 and :30 is a slot. Baseline schedules fill every slot, rotating
through the pool without repeating until each has been used. Overlay
schedules replace the baseline for the slots they match. When two overlays
claim the same slot, the higher priority wins.
"""

from __future__ import annotations

import calendar
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.models import (
    DAY_LABELS,
    DAY_NAMES,
    MONTH_LABELS,
    Announcement,
    Schedule,
    StoreDocument,
    format_hhmm,
    parse_hhmm,
)
from app.slots import Slot, as_local, iter_day_slots, iter_slots, slot_from_parts, zone_for

CONFLICT_HORIZON_DAYS = 70


@dataclass(frozen=True)
class ResolvedSlot:
    slot: Slot
    fire_at: datetime
    schedule: Schedule | None
    announcement: Announcement | None
    source: str
    warning: str | None = None

    @property
    def label(self) -> str:
        if self.source == "silence":
            return "Silence"
        if self.announcement is not None:
            return self.announcement.name
        if self.schedule is not None:
            return self.schedule.name
        return "Nothing scheduled"


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
        wanted = nth_weekday_date(slot.day.year, slot.day.month, weekday, schedule.occurrence or 1)
        return slot.day == wanted
    if schedule.kind == "once":
        return slot.day == schedule.on_date and stamp in schedule.slots
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
    timezone_name: str,
    allowed_stamps: list[str],
) -> bool:
    if slot.day < start_day:
        return False
    event = slot_from_parts(event_day, event_stamp, zone_for(timezone_name))
    if event is None or slot.center >= event.center:
        return False
    if allowed_stamps and slot.stamp not in allowed_stamps:
        return False
    return True


def _range_matches(schedule: Schedule, slot: Slot) -> bool:
    if schedule.start_date is None or schedule.event_date is None or not schedule.event_slot:
        return False
    return _countdown_occupies(
        slot,
        schedule.start_date,
        schedule.event_date,
        schedule.event_slot,
        schedule.timezone,
        schedule.slots,
    )


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return year, month


def _monthly_range_matches(schedule: Schedule, slot: Slot) -> bool:
    if not schedule.days or schedule.occurrence is None or not schedule.event_slot or schedule.days_before is None:
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
                    schedule.timezone,
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


def effective_baseline_order(document: StoreDocument) -> list[str]:
    bag = [item.id for item in document.enabled_baselines()]
    if not bag:
        return []
    current = [item for item in document.rotation.order if item in bag]
    missing = [item for item in bag if item not in current]
    if not current:
        order = list(bag)
        if document.settings.baseline_randomize:
            random.Random().shuffle(order)
        return order
    return current + missing


def peek_baseline(document: StoreDocument) -> Schedule | None:
    order = effective_baseline_order(document)
    if not order:
        return None
    index = document.rotation.index % len(order)
    return document.schedule_by_id(order[index])


def next_rotation(document: StoreDocument, slot_key: str) -> tuple[list[str], int, str]:
    """Advance the bag after a baseline owns `slot_key`. Same slot is a no-op (busy retry)."""
    if document.rotation.last_consumed_slot == slot_key:
        return (
            document.rotation.order or effective_baseline_order(document),
            document.rotation.index,
            slot_key,
        )
    order = effective_baseline_order(document)
    if not order:
        return [], 0, slot_key
    index = document.rotation.index % len(order)
    index += 1
    if index >= len(order):
        index = 0
        if document.settings.baseline_randomize:
            order = list(order)
            random.Random().shuffle(order)
    return order, index, slot_key


def _resolved(slot: Slot, schedule: Schedule | None, document: StoreDocument) -> ResolvedSlot:
    if schedule is None:
        return ResolvedSlot(
            slot=slot,
            fire_at=slot.center,
            schedule=None,
            announcement=None,
            source="none",
            warning="No enabled baseline schedule. Add one so empty slots have something to say.",
        )
    announcement = document.announcement_by_id(schedule.announcement_id)
    offset = 0 if schedule.kind == "baseline" else schedule.offset_minutes
    fire_at = slot.fire_at(offset)
    if schedule.is_silence:
        return ResolvedSlot(
            slot=slot,
            fire_at=fire_at,
            schedule=schedule,
            announcement=None,
            source="silence",
        )
    if announcement is None:
        return ResolvedSlot(
            slot=slot,
            fire_at=fire_at,
            schedule=schedule,
            announcement=None,
            source="none",
            warning=f"{schedule.name} points at a missing announcement.",
        )
    if schedule.kind == "emergency":
        source = "emergency"
    elif schedule.kind == "baseline":
        source = "baseline"
    else:
        source = "overlay"
    return ResolvedSlot(
        slot=slot,
        fire_at=fire_at,
        schedule=schedule,
        announcement=announcement,
        source=source,
    )


def resolve_slot(document: StoreDocument, slot: Slot) -> ResolvedSlot:
    winner, _ties = pick_overlay(document.schedules, slot)
    if winner is not None:
        return _resolved(slot, winner, document)
    return _resolved(slot, peek_baseline(document), document)


def resolve_window(
    document: StoreDocument,
    after: datetime,
    until: datetime,
) -> list[ResolvedSlot]:
    """Upcoming slots with a simulated baseline walk (does not persist rotation)."""
    zone = zone_for(document.settings.timezone)
    order = effective_baseline_order(document)
    index = document.rotation.index % len(order) if order else 0
    results: list[ResolvedSlot] = []
    for slot in iter_slots(after, until, zone):
        winner, _ties = pick_overlay(document.schedules, slot)
        if winner is not None:
            results.append(_resolved(slot, winner, document))
            continue
        if not order:
            results.append(_resolved(slot, None, document))
            continue
        baseline = document.schedule_by_id(order[index % len(order)])
        results.append(_resolved(slot, baseline, document))
        index += 1
        if index >= len(order):
            index = 0
    return results


def next_transmission(document: StoreDocument, after: datetime) -> ResolvedSlot | None:
    zone = zone_for(document.settings.timezone)
    until = as_local(after, zone) + timedelta(days=3)
    for item in resolve_window(document, after, until):
        if item.source in {"baseline", "overlay", "emergency"} and item.announcement is not None:
            return item
    return None


def find_conflicts(
    candidate: Schedule,
    document: StoreDocument,
    *,
    skip_id: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Same slot, same priority, two enabled overlays."""
    if not candidate.enabled or not candidate.is_overlay:
        return []
    zone = zone_for(document.settings.timezone)
    start = now or datetime.now(zone)
    until = start + timedelta(days=CONFLICT_HORIZON_DAYS)
    others = [
        item
        for item in document.schedules
        if item.enabled and item.is_overlay and item.id != (skip_id or candidate.id)
    ]
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    probe = candidate.model_copy(update={"enabled": True})
    for slot in iter_slots(start - timedelta(microseconds=1), until, zone):
        if not overlay_matches(probe, slot):
            continue
        _winner, ties = pick_overlay([probe, *others], slot)
        rivals = [item for item in ties if item.id != probe.id and item.priority == probe.priority]
        for rival in rivals:
            key = f"{rival.id}:{slot.key}"
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "name": rival.name,
                    "summary": summarize(rival),
                    "slot": slot.stamp,
                    "at": slot.center.isoformat(),
                }
            )
            break
        if hits:
            break
    return hits


def offset_allowed(offset_minutes: int, window: int) -> bool:
    return -window <= offset_minutes <= window


def _short_date(value: date) -> str:
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def _month_day(value: date) -> str:
    return f"{value.strftime('%b')} {value.day}"


def _join_en(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _group_circular(indices: list[int], modulus: int) -> list[tuple[int, int]]:
    """Inclusive (start, end) runs. A wrap-around run has start > end."""
    if not indices:
        return []
    ordered = sorted(set(indices))
    if len(ordered) == modulus:
        return [(0, modulus - 1)]
    runs: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        runs.append((start, prev))
        start = prev = value
    runs.append((start, prev))
    if len(runs) >= 2 and runs[0][0] == 0 and runs[-1][1] == modulus - 1:
        wrapped = (runs[-1][0], runs[0][1])
        runs = runs[1:-1] + [wrapped]
    return runs


def _slot_index(stamp: str) -> int:
    hour, minute = parse_hhmm(stamp)
    return hour * 2 + (1 if minute == 30 else 0)


def _stamp_from_index(index: int) -> str:
    return format_hhmm(index // 2, 0 if index % 2 == 0 else 30)


def _day_phrase(days: list[str], plural: bool) -> str:
    if not days:
        return ""
    if set(days) >= set(DAY_NAMES):
        return "every day"
    runs = _group_circular([DAY_NAMES.index(day) for day in days], len(DAY_NAMES))
    parts: list[str] = []
    for start, end in runs:
        if start == end:
            label = DAY_LABELS[DAY_NAMES[start]]
            parts.append(label + ("s" if plural else ""))
        else:
            parts.append(f"{DAY_LABELS[DAY_NAMES[start]]}–{DAY_LABELS[DAY_NAMES[end]]}")
    return _join_en(parts)


def _on_days(days: list[str], *, plural: bool) -> str:
    phrase = _day_phrase(days, plural)
    if not phrase:
        return ""
    if phrase == "every day":
        return " every day"
    return f" on {phrase}"


def _slot_phrase(slots: list[str]) -> str:
    if not slots:
        return "every slot"
    indices = [_slot_index(stamp) for stamp in slots]
    modulus = 48
    if len(set(indices)) == modulus:
        return "every slot"
    parts: list[str] = []
    for start, end in _group_circular(indices, modulus):
        start_stamp = _stamp_from_index(start)
        end_stamp = _stamp_from_index(end)
        if start == end:
            parts.append(f"the {start_stamp} slot")
        else:
            parts.append(f"the {start_stamp}–{end_stamp} slots")
    return _join_en(parts)


def _offset_phrase(offset_minutes: int) -> str:
    if offset_minutes == 0:
        return ""
    minutes = abs(offset_minutes)
    unit = "minute" if minutes == 1 else "minutes"
    if offset_minutes < 0:
        return f", {minutes} {unit} early"
    return f", {minutes} {unit} late"


def summarize(schedule: Schedule) -> str:
    offset = _offset_phrase(schedule.offset_minutes)
    silence = " (silence — no transmission)" if schedule.is_silence else ""
    if schedule.kind == "baseline":
        return "Baseline — every hour and half-hour"
    if schedule.kind == "emergency":
        return f"Emergency — every slot until cleared{offset}{silence}"
    if schedule.kind == "weekly":
        return f"{_slot_phrase(schedule.slots)}{_on_days(schedule.days, plural=True)}{offset}{silence}"
    if schedule.kind == "monthly":
        occ = _ordinal(schedule.occurrence or 1)
        weekday = DAY_LABELS[schedule.days[0]] if schedule.days else "day"
        extra = ""
        if schedule.skip_months:
            extra = f", except {_join_en([MONTH_LABELS[month] for month in schedule.skip_months])}"
        return f"{_slot_phrase(schedule.slots)} on the {occ} {weekday} of every month{offset}{extra}{silence}"
    if schedule.kind == "once":
        when = _short_date(schedule.on_date) if schedule.on_date else "once"
        return f"{_slot_phrase(schedule.slots)} on {when}{offset}{silence}"
    if schedule.kind == "range":
        start = _month_day(schedule.start_date) if schedule.start_date else "?"
        event_day = _month_day(schedule.event_date) if schedule.event_date else "?"
        event_slot = schedule.event_slot or "?"
        which = _slot_phrase(schedule.slots)
        return (
            f"{which} from {start} up to the {event_day} {event_slot} slot "
            f"(not that slot or later that day){offset}{silence}"
        )
    if schedule.kind == "monthly_range":
        occ = _ordinal(schedule.occurrence or 1)
        weekday = DAY_LABELS[schedule.days[0]] if schedule.days else "day"
        lead = schedule.days_before if schedule.days_before is not None else "?"
        event_slot = schedule.event_slot or "?"
        extra = ""
        if schedule.skip_months:
            extra = f", except {_join_en([MONTH_LABELS[month] for month in schedule.skip_months])}"
        which = _slot_phrase(schedule.slots)
        unit = "day" if lead == 1 else "days"
        return (
            f"{which} for {lead} {unit} before the {occ} {weekday} {event_slot} slot "
            f"each month (not that slot or later that day){offset}{extra}{silence}"
        )
    return schedule.kind


def schedule_warning(
    schedule: Schedule,
    document: StoreDocument,
    now: datetime | None = None,
) -> str | None:
    if not schedule.enabled:
        return "Won't play — this schedule is disabled."
    if schedule.kind != "baseline" and schedule.is_silence:
        pass
    elif document.announcement_by_id(schedule.announcement_id) is None:
        return "Won't play — the announcement is missing."
    elif not (document.announcement_by_id(schedule.announcement_id).text or "").strip():
        return "Won't play — announcement text is empty."
    zone = zone_for(schedule.timezone)
    when = now or datetime.now(zone)
    if schedule.kind == "once" and schedule.on_date and schedule.on_date < when.date():
        return "Won't play — the date has already passed."
    if schedule.kind == "range" and schedule.event_date and schedule.event_date < when.date():
        return "Won't play — the event date has already passed."
    if schedule.kind == "baseline" and not schedule.enabled:
        return "Won't play — this schedule is disabled."
    return None


def document_warnings(document: StoreDocument) -> list[str]:
    warnings: list[str] = []
    if not document.announcements:
        warnings.append("Create at least one announcement, then a baseline schedule that uses it.")
    elif not document.enabled_baselines():
        warnings.append("Add an enabled baseline schedule. Empty slots stay silent until you do.")
    return warnings


def clock_for_day(document: StoreDocument, day: date) -> list[ResolvedSlot]:
    zone = zone_for(document.settings.timezone)
    start = datetime(day.year, day.month, day.day, tzinfo=zone) - timedelta(microseconds=1)
    until = datetime(day.year, day.month, day.day, tzinfo=zone) + timedelta(days=1)
    wanted = {slot.key for slot in iter_day_slots(day, zone)}
    return [item for item in resolve_window(document, start, until) if item.slot.key in wanted]


def next_runs_by_schedule(document: StoreDocument, now: datetime) -> dict[str, datetime]:
    found: dict[str, datetime] = {}
    until = now + timedelta(days=CONFLICT_HORIZON_DAYS)
    for item in resolve_window(document, now, until):
        if item.schedule is None or item.schedule.id in found:
            continue
        found[item.schedule.id] = item.fire_at
    return found


def flatten_schedule_row(
    schedule: Schedule,
    document: StoreDocument,
    now: datetime,
    next_runs: dict[str, datetime] | None = None,
) -> dict[str, object]:
    announcement = document.announcement_by_id(schedule.announcement_id)
    nxt = (next_runs or {}).get(schedule.id)
    if nxt is None and next_runs is None:
        nxt = next_runs_by_schedule(document, now).get(schedule.id)
    last = schedule.last_run_at
    return {
        "schedule_id": schedule.id,
        "name": schedule.name,
        "kind": schedule.kind,
        "enabled": schedule.enabled,
        "announcement_id": schedule.announcement_id,
        "announcement_name": announcement.name if announcement else None,
        "summary": summarize(schedule),
        "warning": schedule_warning(schedule, document, now),
        "priority": schedule.priority,
        "offset_minutes": schedule.offset_minutes,
        "last_run_at": last.isoformat() if last else None,
        "next_run_at": nxt.isoformat() if nxt else None,
    }
