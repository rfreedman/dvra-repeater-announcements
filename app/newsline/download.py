"""Fetch the latest Amateur Radio Newsline report."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import feedparser
import requests
from feedparser import FeedParserDict

log = logging.getLogger("app.newsline")

FEED_URL = "https://www.arnewsline.org/?format=rss"
SCRIPT_URL_TEMPLATE = "https://www.arnewsline.org/s/nsln{report_number}.txt"
REPORT_NUMBER_RE = re.compile(r"Report\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class FeedReport:
    report_number: int
    title: str
    audio_url: str


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


def latest_report(feed: FeedParserDict) -> FeedReport | None:
    for entry in getattr(feed, "entries", []) or []:
        number = extract_report_number(entry)
        if number is None:
            continue
        enclosures = getattr(entry, "enclosures", None)
        if not enclosures:
            log.warning("Newsline report %s has no audio enclosure", number)
            continue
        title = getattr(entry, "title", "") or f"Report {number}"
        return FeedReport(report_number=number, title=title, audio_url=enclosures[0].href)
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


def script_url(report_number: int) -> str:
    return SCRIPT_URL_TEMPLATE.format(report_number=report_number)
