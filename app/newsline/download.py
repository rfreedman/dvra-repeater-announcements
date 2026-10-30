"""Fetch the latest Amateur Radio Newsline report."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import feedparser
import requests
from feedparser import FeedParserDict

log = logging.getLogger("app.newsline")

FEED_URL = "https://www.arnewsline.org/?format=rss"
REPORT_NUMBER_RE = re.compile(r"Report\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class FeedReport:
    report_number: int
    title: str
    audio_url: str
    script_url: str


class _LabeledLinkParser(HTMLParser):
    """Collect hrefs keyed by the visible text of each <a> (first wins)."""

    def __init__(self) -> None:
        super().__init__()
        self.links: dict[str, str] = {}
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._href is not None:
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        label = "".join(self._parts).strip().casefold()
        if label and label not in self.links:
            self.links[label] = self._href
        self._href = None
        self._parts = []


def fetch_feed() -> FeedParserDict:
    return feedparser.parse(FEED_URL)


def extract_report_number(entry: FeedParserDict) -> int | None:
    if getattr(entry, "enclosures", None):
        path = unquote(urlparse(entry.enclosures[0].href).path)
        match = REPORT_NUMBER_RE.search(os.path.basename(path))
        if match:
            return int(match.group(1))
    title = getattr(entry, "title", "") or ""
    match = REPORT_NUMBER_RE.search(title)
    if match:
        return int(match.group(1))
    return None


def _entry_html(entry: FeedParserDict) -> str:
    parts: list[str] = []
    for attr in ("summary", "description"):
        value = getattr(entry, attr, None) or ""
        if value:
            parts.append(value)
    for item in getattr(entry, "content", None) or []:
        value = item.get("value", "") if isinstance(item, dict) else getattr(item, "value", "") or ""
        if value:
            parts.append(value)
    return "\n".join(parts)


def _enclosure_href(entry: FeedParserDict) -> str | None:
    enclosures = getattr(entry, "enclosures", None)
    if not enclosures:
        return None
    first = enclosures[0]
    href = getattr(first, "href", None)
    if href:
        return href
    if isinstance(first, dict):
        return first.get("href") or first.get("url")
    return None


def entry_download_urls(entry: FeedParserDict) -> tuple[str | None, str | None]:
    """Return (audio_url, script_url) from the enclosure and SCRIPT/AUDIO links."""
    parser = _LabeledLinkParser()
    parser.feed(_entry_html(entry))
    audio = _enclosure_href(entry) or parser.links.get("audio")
    script = parser.links.get("script")
    return audio, script


def latest_report(feed: FeedParserDict) -> FeedReport | None:
    for entry in getattr(feed, "entries", []) or []:
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
        title = getattr(entry, "title", "") or f"Report {number}"
        return FeedReport(
            report_number=number,
            title=title,
            audio_url=audio_url,
            script_url=script_url,
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
                    handle.write(chunk)
        temporary.replace(dest)
    finally:
        if temporary.exists():
            temporary.unlink()
    log.info("Saved %s", dest)
