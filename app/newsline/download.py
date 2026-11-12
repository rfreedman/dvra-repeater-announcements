"""Fetch the latest Amateur Radio Newsline report."""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import cast, override
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests

log = logging.getLogger("app.newsline")

FEED_URL = "https://www.arnewsline.org/?format=rss"
REPORT_NUMBER_RE = re.compile(r"Report\s*(\d+)", re.IGNORECASE)

# feedparser values are untyped; treat entries as opaque objects and narrow at use sites.
FeedEntry = object
Feed = object


@dataclass(frozen=True)
class FeedReport:
    report_number: int
    title: str
    audio_url: str
    script_url: str
    published_on: datetime | None = None


class _LabeledLinkParser(HTMLParser):
    """Collect hrefs keyed by the visible text of each <a> (first wins)."""

    links: dict[str, str]
    _href: str | None
    _parts: list[str]

    def __init__(self) -> None:
        super().__init__()
        self.links = {}
        self._href = None
        self._parts = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._href is not None:
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._parts = []

    @override
    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        label = "".join(self._parts).strip().casefold()
        if label and label not in self.links:
            self.links[label] = self._href
        self._href = None
        self._parts = []


def fetch_feed() -> Feed:
    return cast(object, feedparser.parse(FEED_URL))


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _enclosure_href_string(enclosure: object) -> str | None:
    if isinstance(enclosure, Mapping):
        mapping = cast(Mapping[str, object], enclosure)
        for key in ("href", "url"):
            raw = mapping.get(key)
            if isinstance(raw, str) and raw:
                return raw
        return None
    href = getattr(enclosure, "href", None)
    return href if isinstance(href, str) and href else None


def extract_report_number(entry: FeedEntry) -> int | None:
    enclosures = cast(object, getattr(entry, "enclosures", None))
    if isinstance(enclosures, Sequence) and not isinstance(enclosures, (str, bytes)) and enclosures:
        href = _enclosure_href_string(enclosures[0])
        if href:
            path = unquote(urlparse(href).path)
            match = REPORT_NUMBER_RE.search(os.path.basename(path))
            if match:
                return int(match.group(1))
    title = _as_str(getattr(entry, "title", ""))
    match = REPORT_NUMBER_RE.search(title)
    if match:
        return int(match.group(1))
    return None


def _content_value(item: object) -> str:
    if isinstance(item, Mapping):
        return _as_str(cast(Mapping[str, object], item).get("value", ""))
    return _as_str(getattr(item, "value", ""))


def _entry_html(entry: FeedEntry) -> str:
    parts: list[str] = []
    for attr in ("summary", "description"):
        value = _as_str(getattr(entry, attr, None))
        if value:
            parts.append(value)
    content = cast(object, getattr(entry, "content", None))
    items: Sequence[object]
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        items = content
    else:
        items = []
    for item in items:
        value = _content_value(item)
        if value:
            parts.append(value)
    return "\n".join(parts)


def _enclosure_href(entry: FeedEntry) -> str | None:
    enclosures = cast(object, getattr(entry, "enclosures", None))
    if not isinstance(enclosures, Sequence) or isinstance(enclosures, (str, bytes)) or not enclosures:
        return None
    return _enclosure_href_string(enclosures[0])


def entry_published_on(entry: FeedEntry, *, zone: ZoneInfo) -> datetime | None:
    """Feed ``pubDate`` as a timezone-aware instant in ``zone``.

    ``published_parsed`` is UTC. A missing or unreadable date returns ``None``.
    """
    moment: datetime | None = None
    parsed = getattr(entry, "published_parsed", None)
    if isinstance(parsed, time.struct_time):
        moment = datetime(
            parsed.tm_year,
            parsed.tm_mon,
            parsed.tm_mday,
            parsed.tm_hour,
            parsed.tm_min,
            parsed.tm_sec,
            tzinfo=timezone.utc,
        )
    else:
        raw = _as_str(getattr(entry, "published", ""))
        if raw:
            try:
                moment = parsedate_to_datetime(raw)
            except (TypeError, ValueError, IndexError):
                moment = None
            if moment is not None and moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
    if moment is None:
        return None
    return moment.astimezone(zone).replace(microsecond=0)


def entry_download_urls(entry: FeedEntry) -> tuple[str | None, str | None]:
    """Return (audio_url, script_url) from the enclosure and SCRIPT/AUDIO links."""
    parser = _LabeledLinkParser()
    parser.feed(_entry_html(entry))
    audio = _enclosure_href(entry) or parser.links.get("audio")
    script = parser.links.get("script")
    return audio, script


def latest_report(feed: Feed) -> FeedReport | None:
    entries_raw = cast(object, getattr(feed, "entries", None))
    entries: Sequence[object]
    if isinstance(entries_raw, Sequence) and not isinstance(entries_raw, (str, bytes)):
        entries = entries_raw
    else:
        entries = []
    for entry in entries:
        number = extract_report_number(entry)
        if number is None:
            continue
        audio_url, script_url = entry_download_urls(entry)
        if not audio_url:
            log.warning("Newsline report %s has no audio URL", number)
            continue
        if not script_url:
            log.warning("Newsline report %s has no SCRIPT download link", number)
            continue
        title = _as_str(getattr(entry, "title", "")) or f"Report {number}"
        from app.config import TIMEZONE
        from app.slots import zone_for

        return FeedReport(
            report_number=number,
            title=title,
            audio_url=audio_url,
            script_url=script_url,
            published_on=entry_published_on(entry, zone=zone_for(TIMEZONE)),
        )
    return None


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".partial")
    response = requests.get(url, stream=True, timeout=120)
    if response.status_code != 200:
        raise OSError(f"Download failed for {url} (HTTP {response.status_code})")
    try:
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    _ = handle.write(chunk)
        _ = temporary.replace(dest)
    finally:
        if temporary.exists():
            temporary.unlink()
    log.info("Saved %s", dest)
