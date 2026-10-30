"""Newsline RSS entries expose SCRIPT and AUDIO download URLs as-is."""

from __future__ import annotations

import feedparser

from app.newsline.download import entry_download_urls, latest_report


def _feed(items_xml: str) -> feedparser.FeedParserDict:
    return feedparser.parse(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://www.rssboard.org/media-rss">
  <channel>
    <title>Amateur Radio Newsline</title>
    {items_xml}
  </channel>
</rss>
"""
    )


def _item(
    *,
    number: int,
    description: str,
    enclosure: str | None = None,
) -> str:
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


def test_latest_report_uses_script_and_enclosure_hrefs_unchanged():
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


def test_audio_link_is_used_when_there_is_no_enclosure():
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
    audio_url, script_url = entry_download_urls(feed.entries[0])
    assert audio_url == audio
    assert script_url == script
    report = latest_report(feed)
    assert report is not None
    assert report.audio_url == audio
    assert report.script_url == script


def test_latest_report_skips_an_entry_that_has_no_script_link():
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


def test_latest_report_is_none_when_audio_and_script_are_missing():
    feed = _feed(
        _item(
            number=2557,
            description="<p>No download links this week.</p>",
        )
    )
    assert latest_report(feed) is None
    assert entry_download_urls(feed.entries[0]) == (None, None)
