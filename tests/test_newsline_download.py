"""Newsline RSS entries expose SCRIPT and AUDIO download URLs as-is."""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import feedparser
import pytest

from app.newsline.download import entry_download_urls, entry_published_on, latest_report
from app.newsline.state import load_state, published_on_text


def _feed(items_xml: str) -> feedparser.FeedParserDict:
    return cast(
        feedparser.FeedParserDict,
        feedparser.parse(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://www.rssboard.org/media-rss">
  <channel>
    <title>Amateur Radio Newsline</title>
    {items_xml}
  </channel>
</rss>
"""
        ),
    )


def _entries(feed: feedparser.FeedParserDict) -> list[object]:
    entries = cast(object, feed.get("entries"))
    if not isinstance(entries, list):
        raise AssertionError("feed entries must be a list")
    return cast(list[object], entries)


def _item(*, number: int, description: str, enclosure: str | None = None) -> str:
    enclosure_xml = ""
    if enclosure is not None:
        enclosure_xml = (
            f'<enclosure url="{enclosure}" length="1" type="audio/mpeg"/>'
        )
    return f"""
    <item>
      <title>Amateur Radio Newsline Report {number}</title>
      <description><![CDATA[{description}]]></description>
      {enclosure_xml}
    </item>
    """


def test_latest_report_uses_script_and_enclosure_hrefs_unchanged() -> None:
    script = "https://arnewsline7.squarespace.com/s/NSLN2557.txt"
    audio = "https://www.arnewsline.org/s/Report2557.mp3"
    enclosure = "https://cdn.example.invalid/t/abc/Report2557.mp3"
    feed = _feed(
        _item(
            number=2557,
            description=(
                f'<p><a href="{script}">SCRIPT</a></p>'
                f'<p><a href="{audio}">AUDIO</a></p>'
            ),
            enclosure=enclosure,
        )
    )
    report = latest_report(feed)
    assert report is not None
    assert report.report_number == 2557
    assert report.audio_url == enclosure
    assert report.script_url == script


def test_audio_link_is_used_when_there_is_no_enclosure() -> None:
    script = "https://www.arnewsline.org/s/nsln2556.txt"
    audio = "https://files.example.invalid/odd/path/show-2556.mp3"
    feed = _feed(
        _item(
            number=2556,
            description=(
                f'<a href="{script}"><strong>SCRIPT</strong></a>'
                f'<a href="{audio}">AUDIO</a>'
            ),
        )
    )
    audio_url, script_url = entry_download_urls(_entries(feed)[0])
    assert audio_url == audio
    assert script_url == script
    report = latest_report(feed)
    assert report is not None
    assert report.audio_url == audio
    assert report.script_url == script


def test_latest_report_skips_an_entry_that_has_no_script_link() -> None:
    feed = _feed(
        _item(
            number=2557,
            description='<a href="https://example.invalid/Report2557.mp3">AUDIO</a>',
            enclosure="https://cdn.example.invalid/Report2557.mp3",
        )
        + _item(
            number=2556,
            description=(
                '<a href="https://example.invalid/nsln2556.txt">SCRIPT</a>'
                '<a href="https://example.invalid/Report2556.mp3">AUDIO</a>'
            ),
            enclosure="https://cdn.example.invalid/Report2556.mp3",
        )
    )
    report = latest_report(feed)
    assert report is not None
    assert report.report_number == 2556
    assert report.script_url == "https://example.invalid/nsln2556.txt"


def test_published_on_is_the_station_clock() -> None:
    zone = ZoneInfo("America/New_York")
    early = time.struct_time((2026, 11, 5, 0, 30, 0, 3, 309, 0))
    afternoon = time.struct_time((2026, 11, 5, 18, 0, 0, 3, 309, 0))
    assert entry_published_on(SimpleNamespace(published_parsed=early), zone=zone) == datetime(
        2026, 11, 4, 19, 30, tzinfo=zone
    )
    assert entry_published_on(SimpleNamespace(published_parsed=afternoon), zone=zone) == datetime(
        2026, 11, 5, 13, 0, tzinfo=zone
    )
    assert entry_published_on(SimpleNamespace(), zone=zone) is None


def test_latest_report_keeps_the_feed_publication_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.TIMEZONE", "America/New_York")
    script = "https://example.invalid/nsln2558.txt"
    audio = "https://cdn.example.invalid/Report2558.mp3"
    feed = _feed(
        """
    <item>
      <title>Amateur Radio Newsline Report 2558</title>
      <pubDate>Thu, 05 Nov 2026 00:30:00 GMT</pubDate>
      <description><![CDATA[
        <a href="https://example.invalid/nsln2558.txt">SCRIPT</a>
        <a href="https://cdn.example.invalid/Report2558.mp3">AUDIO</a>
      ]]></description>
      <enclosure url="https://cdn.example.invalid/Report2558.mp3" length="1" type="audio/mpeg"/>
    </item>
    """
    )
    report = latest_report(feed)
    assert report is not None
    assert report.audio_url == audio
    assert report.script_url == script
    assert report.published_on == datetime(2026, 11, 4, 19, 30, tzinfo=ZoneInfo("America/New_York"))


def test_date_only_published_on_loads_without_a_clock(tmp_path: Path) -> None:
    _ = (tmp_path / "state.json").write_text('{"published_on": "2026-11-05"}', encoding="utf-8")
    state = load_state(tmp_path)
    assert state.published_on == datetime(2026, 11, 5)
    assert state.published_on is not None and state.published_on.tzinfo is None
    assert published_on_text(state.published_on) == "2026-11-05"


def test_latest_report_is_none_when_audio_and_script_are_missing() -> None:
    feed = _feed(
        _item(
            number=2557,
            description="<p>No download links this week.</p>",
        )
    )
    assert latest_report(feed) is None
    assert entry_download_urls(_entries(feed)[0]) == (None, None)
