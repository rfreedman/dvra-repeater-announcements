from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import DAY_LABELS, DAY_NAMES, Announcement, Schedule, parse_hhmm


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


def next_run_at(schedule: Schedule, after: datetime) -> datetime | None:
    if not schedule.enabled:
        return None
    if schedule.kind == "hourly":
        return _next_hourly(schedule, after)
    if schedule.kind == "daily":
        return _next_daily(schedule, after)
    if schedule.kind == "weekly":
        return _next_weekly(schedule, after)
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
    extra = summarize_exclusions(schedule)
    if schedule.kind == "hourly":
        stamps = [f":{minute:02d}" for minute in _hourly_minutes(schedule)]
        base = f"Every hour at {_join_en(stamps)}"
    elif schedule.kind == "daily":
        times = _join_en(list(schedule.times))
        base = f"{times} every day"
    elif schedule.kind == "weekly":
        times = _join_en(list(schedule.times))
        base = f"{times} on {_day_phrase(schedule.days, plural=True)}"
    else:
        when = aware(schedule.at, schedule) if schedule.at else None
        if when is None:
            return "Once"
        base = f"Once on {when.strftime('%b')} {when.day}, {when.year} at {when.strftime('%H:%M')}"
        extra = ""
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
    until = start + timedelta(days=8)
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
