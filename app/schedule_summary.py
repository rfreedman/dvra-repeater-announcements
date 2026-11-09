"""Plain-language descriptions of a schedule."""

from __future__ import annotations

from datetime import date
from typing import assert_never

from app.models import (
    DAY_LABELS,
    DAY_NAMES,
    MONTH_LABELS,
    DayName,
    Schedule,
    format_hhmm,
    parse_hhmm,
)


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


def _day_phrase(days: list[str] | list[DayName], plural: bool) -> str:
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


def _on_days(days: list[str] | list[DayName], *, plural: bool) -> str:
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
        occ = _ordinal(schedule.occurrence if schedule.occurrence is not None else 1)
        weekday = DAY_LABELS[schedule.days[0]] if schedule.days else "day"
        extra = ""
        if schedule.skip_months:
            extra = f", except {_join_en([MONTH_LABELS[month] for month in schedule.skip_months])}"
        return f"{_slot_phrase(schedule.slots)} on the {occ} {weekday} of every month{offset}{extra}{silence}"
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
        occ = _ordinal(schedule.occurrence if schedule.occurrence is not None else 1)
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
    assert_never(schedule.kind)
