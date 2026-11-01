"""Intended Friday from a Newsline script, and the days that report may air."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from app.slots import zone_for

_FRIDAY_RE = re.compile(
    r"(?:for|release date of)\s+Friday,\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_release_friday(text: str) -> date | None:
    """The Friday named in the script header, or ``None`` if it is not there."""
    match = _FRIDAY_RE.search(text)
    if match is None:
        return None
    month = _MONTHS.get(match.group(1).casefold())
    if month is None:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


def air_dates(friday: date) -> tuple[date, date]:
    """Thursday of the release week through the following Thursday, inclusive."""
    return friday - timedelta(days=1), friday + timedelta(days=6)


def in_air_window(friday: date, today: date) -> bool:
    first, last = air_dates(friday)
    return first <= today <= last


def friday_from_script(path: Path) -> date | None:
    if not path.is_file():
        return None
    return parse_release_friday(path.read_text(encoding="utf-8", errors="replace"))


def station_today() -> date:
    from app.store import get_store

    zone = zone_for(get_store().document().settings.timezone)
    return datetime.now(zone).date()
