from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import DAY_LABELS, DAY_NAMES, MONTH_LABELS, Announcement, Schedule, parse_hhmm


def zone_for(schedule: Schedule) -> ZoneInfo:
    return ZoneInfo(schedule.timezone)


def aware(dt: datetime, schedule: Schedule) -> datetime:
    zone = zone_for(schedule)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def weekday_key(dt: datetime) -> str:
    return DAY_NAMES[dt.weekday()]


def _hhmm_minutes(value: str) -> int:
    hour, minute = parse_hhmm(value)
    return hour * 60 + minute


def _in_exclusion_range(stamp_minutes: int, start: str, end: str) -> bool:
    begin = _hhmm_minutes(start)
    finish = _hhmm_minutes(end)
    if begin <= finish:
        return begin <= stamp_minutes <= finish
    return stamp_minutes >= begin or stamp_minutes <= finish


def is_excluded(schedule: Schedule, when: datetime) -> bool:
    if schedule.kind == "once":
        return False
    local = aware(when, schedule)
    day = weekday_key(local)
    stamp = local.hour * 60 + local.minute
    for exclusion in schedule.exclusions:
        if day not in exclusion.days:
            continue
        if not exclusion.start or not exclusion.end:
            return True
        if _in_exclusion_range(stamp, exclusion.start, exclusion.end):
            return True
    return False


def _hourly_minutes(schedule: Schedule) -> list[int]:
    if schedule.minutes:
        return schedule.minutes
    if schedule.minute is not None:
        return [schedule.minute]
    return [0]


def _next_hourly(schedule: Schedule, after: datetime) -> datetime | None:
    local = aware(after, schedule)
    minutes = _hourly_minutes(schedule)
    hour_start = local.replace(minute=0, second=0, microsecond=0)
    for offset in range(24 * 14 + 2):
        base = hour_start + timedelta(hours=offset)
        for minute in minutes:
            candidate = base.replace(minute=minute, second=0, microsecond=0)
            if candidate > local and not is_excluded(schedule, candidate):
                return candidate
    return None


def _next_daily(schedule: Schedule, after: datetime) -> datetime | None:
    local = aware(after, schedule)
    slots = [parse_hhmm(item) for item in schedule.times]
    day = local.date()
    for _ in range(14 * len(slots) + 2):
        for hour, minute in slots:
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone_for(schedule))
            if candidate > local and not is_excluded(schedule, candidate):
                return candidate
        day = day + timedelta(days=1)
        local = datetime(day.year, day.month, day.day, tzinfo=zone_for(schedule)) - timedelta(microseconds=1)
    return None


def _next_weekly(schedule: Schedule, after: datetime) -> datetime | None:
    local = aware(after, schedule)
    slots = [parse_hhmm(item) for item in schedule.times]
    allowed = set(schedule.days)
    day = local.date()
    for _ in range(14 * 7 + 2):
        if DAY_NAMES[day.weekday()] in allowed:
            for hour, minute in slots:
                candidate = datetime(
                    day.year, day.month, day.day, hour, minute, tzinfo=zone_for(schedule)
                )
                if candidate > local and not is_excluded(schedule, candidate):
                    return candidate
        day = day + timedelta(days=1)
        local = datetime(day.year, day.month, day.day, tzinfo=zone_for(schedule)) - timedelta(
            microseconds=1
        )
    return None


def nth_weekday_date(year: int, month: int, weekday: int, occurrence: int) -> date | None:
    days = [
        item
        for item in calendar.Calendar(firstweekday=0).itermonthdates(year, month)
        if item.month == month and item.weekday() == weekday
    ]
    if not days:
        return None
    if occurrence == -1:
        return days[-1]
    index = occurrence - 1
    if index < 0 or index >= len(days):
        return None
    return days[index]


def _shift_month(year: int, month: int, delta: int = 1) -> tuple[int, int]:
    month += delta
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return year, month


def monthly_dates_in_month(schedule: Schedule, year: int, month: int) -> list[date]:
    if month in set(schedule.skip_months):
        return []
    if schedule.monthdays:
        last = calendar.monthrange(year, month)[1]
        return [date(year, month, day) for day in schedule.monthdays if 1 <= day <= last]
    if not schedule.days or schedule.occurrence is None:
        return []
    found = nth_weekday_date(year, month, DAY_NAMES.index(schedule.days[0]), schedule.occurrence)
    return [found] if found else []


def _next_monthly(schedule: Schedule, after: datetime) -> datetime | None:
    local = aware(after, schedule)
    slots = [parse_hhmm(item) for item in schedule.times]
    zone = zone_for(schedule)
    year, month = local.year, local.month
    cursor = local
    for _ in range(16):
        for day in monthly_dates_in_month(schedule, year, month):
            for hour, minute in slots:
                candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
                if candidate > cursor and not is_excluded(schedule, candidate):
                    return candidate
        year, month = _shift_month(year, month)
        cursor = datetime(year, month, 1, tzinfo=zone) - timedelta(microseconds=1)
    return None


def next_run_at(schedule: Schedule, after: datetime) -> datetime | None:
    if not schedule.enabled:
        return None
    if schedule.kind == "hourly":
        return _next_hourly(schedule, after)
    if schedule.kind == "daily":
        return _next_daily(schedule, after)
    if schedule.kind == "weekly":
        return _next_weekly(schedule, after)
    if schedule.kind == "monthly":
        return _next_monthly(schedule, after)
    if schedule.at is None:
        return None
    when = aware(schedule.at, schedule)
    return when if when > aware(after, schedule) else None


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


def _month_phrase(months: list[int]) -> str:
    return _join_en([MONTH_LABELS[month] for month in months])


def _day_phrase(days: list[str], plural: bool) -> str:
    names = [DAY_LABELS[day] + ("s" if plural else "") for day in days]
    return _join_en(names)


def summarize_exclusions(schedule: Schedule) -> str:
    return "\n".join(exclusion_lines(schedule))


def exclusion_lines(schedule: Schedule) -> list[str]:
    lines: list[str] = []
    for exclusion in schedule.exclusions:
        if exclusion.start and exclusion.end:
            bit = f"{_day_phrase(exclusion.days, plural=False)} {exclusion.start}–{exclusion.end}"
        else:
            bit = _day_phrase(exclusion.days, plural=True)
        if exclusion.note:
            bit = f"{bit} ({exclusion.note})"
        lines.append(f"except {bit}")
    return lines


def summarize(schedule: Schedule) -> str:
    extra_lines = exclusion_lines(schedule)
    if schedule.kind == "hourly":
        stamps = [f":{minute:02d}" for minute in _hourly_minutes(schedule)]
        base = f"Every hour at {_join_en(stamps)}"
    elif schedule.kind == "daily":
        times = _join_en(list(schedule.times))
        base = f"{times} every day"
    elif schedule.kind == "weekly":
        times = _join_en(list(schedule.times))
        base = f"{times} on {_day_phrase(schedule.days, plural=True)}"
    elif schedule.kind == "monthly":
        times = _join_en(list(schedule.times))
        if schedule.monthdays:
            ords = _join_en([_ordinal(day) for day in schedule.monthdays])
            base = f"{times} on the {ords} of every month"
        else:
            occ = "last" if schedule.occurrence == -1 else _ordinal(schedule.occurrence or 1)
            weekday = DAY_LABELS[schedule.days[0]] if schedule.days else "day"
            base = f"{times} on the {occ} {weekday} of every month"
        if schedule.skip_months:
            extra_lines.append(f"except {_month_phrase(schedule.skip_months)}")
    else:
        when = aware(schedule.at, schedule) if schedule.at else None
        if when is None:
            return "Once"
        base = f"Once on {when.strftime('%b')} {when.day}, {when.year} at {when.strftime('%H:%M')}"
        extra_lines = []
    extra = "\n".join(extra_lines)
    if extra:
        return f"{base}\n{extra}"
    return base


def trigger_warning(
    announcement: Announcement,
    schedule: Schedule | None,
    now: datetime | None = None,
) -> str | None:
    missing: list[str] = []
    if not (announcement.text or "").strip():
        missing.append("announcement text")
    if schedule is None:
        missing.append("a schedule")
    if missing:
        if missing == ["a schedule"]:
            return "Won't play until a schedule is saved."
        if missing == ["announcement text"]:
            return "Won't play — announcement text is empty."
        return "Won't play — missing " + _join_en(missing) + "."
    if not schedule.enabled:
        return "Won't play — this schedule is disabled."
    when = now or datetime.now(zone_for(schedule))
    if next_run_at(schedule, when) is None:
        if schedule.kind == "once":
            return "Won't play — the date and time have already passed."
        return "Won't play — no upcoming run. Check timing and exclusions."
    return None


def iter_fire_times(schedule: Schedule, after: datetime, until: datetime):
    cursor = after
    for _ in range(800):
        nxt = next_run_at(schedule, cursor)
        if nxt is None or nxt >= until:
            return
        yield nxt
        cursor = nxt


def find_conflicts(
    candidate: Schedule,
    others: list[tuple[str, Schedule]],
    now: datetime | None = None,
) -> list[dict[str, str]]:
    if not candidate.enabled:
        return []
    zone = zone_for(candidate)
    start = now or datetime.now(zone)
    kinds = {candidate.kind, *(other.kind for _, other in others)}
    horizon = 70 if "monthly" in kinds else 8
    until = start + timedelta(days=horizon)
    mine = {
        item.astimezone(zone).replace(second=0, microsecond=0)
        for item in iter_fire_times(candidate, start - timedelta(microseconds=1), until)
    }
    hits: list[dict[str, str]] = []
    for name, other in others:
        if not other.enabled:
            continue
        for when in iter_fire_times(other, start - timedelta(microseconds=1), until):
            key = when.astimezone(zone).replace(second=0, microsecond=0)
            if key in mine:
                hits.append(
                    {
                        "name": name,
                        "summary": summarize(other),
                        "at": key.isoformat(),
                    }
                )
                break
    return hits
