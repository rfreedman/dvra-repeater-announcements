"""Intended Friday from the script, and the days that report may air."""

from __future__ import annotations

from datetime import date

from app.newsline.playlist import newsline_unavailable_reason
from app.newsline.release import air_dates, in_air_window, parse_release_friday
from app.newsline.state import NewsState

HEADER_2556 = """Amateur Radio Newsline Report 2556 for Friday, October 23rd, 2026

Amateur Radio Newsline Report Number 2556 with a release date of Friday, October 23rd, 2026 to follow in 5-4-3-2-1.
"""

HEADER_2557 = "Amateur Radio Newsline Report 2557 for Friday, October 30th, 2026\n"


def test_parse_release_friday_from_for_friday_line():
    assert parse_release_friday(HEADER_2557) == date(2026, 10, 30)


def test_parse_release_friday_from_release_date_line():
    text = (
        "Amateur Radio Newsline Report Number 2556 with a "
        "release date of Friday, October 23rd, 2026 to follow in 5-4-3-2-1.\n"
    )
    assert parse_release_friday(text) == date(2026, 10, 23)


def test_parse_release_friday_accepts_st_nd_rd_th():
    assert parse_release_friday("for Friday, January 1st, 2027") == date(2027, 1, 1)
    assert parse_release_friday("for Friday, January 2nd, 2026") == date(2026, 1, 2)
    assert parse_release_friday("for Friday, January 3rd, 2026") == date(2026, 1, 3)
    assert parse_release_friday("for Friday, January 4th, 2026") == date(2026, 1, 4)


def test_parse_release_friday_returns_none_when_missing():
    assert parse_release_friday("Amateur Radio Newsline Report 2556") is None
    assert parse_release_friday("") is None


def test_air_window_is_thursday_through_following_thursday():
    friday = date(2026, 10, 23)
    assert air_dates(friday) == (date(2026, 10, 22), date(2026, 10, 29))
    assert in_air_window(friday, date(2026, 10, 22))
    assert in_air_window(friday, date(2026, 10, 23))
    assert in_air_window(friday, date(2026, 10, 29))
    assert not in_air_window(friday, date(2026, 10, 21))
    assert not in_air_window(friday, date(2026, 10, 30))

    next_friday = date(2026, 10, 30)
    assert air_dates(next_friday) == (date(2026, 10, 29), date(2026, 11, 5))
    assert in_air_window(next_friday, date(2026, 10, 29))
    assert in_air_window(next_friday, date(2026, 11, 5))
    assert not in_air_window(next_friday, date(2026, 11, 6))


def test_expired_last_week_files_are_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    stories = tmp_path / "2556" / "stories"
    stories.mkdir(parents=True)
    (stories / "2556_01_story.mp3").write_bytes(b"mp3")
    state = NewsState(
        report_number=2556,
        split_ok=True,
        release_friday=date(2026, 10, 23),
    )
    assert newsline_unavailable_reason(state, today=date(2026, 10, 30)) == "Newsline is not available"


def test_current_friday_files_are_available(tmp_path, monkeypatch):
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    stories = tmp_path / "2557" / "stories"
    stories.mkdir(parents=True)
    (stories / "2557_01_story.mp3").write_bytes(b"mp3")
    state = NewsState(
        report_number=2557,
        split_ok=True,
        release_friday=date(2026, 10, 30),
    )
    assert newsline_unavailable_reason(state, today=date(2026, 10, 30)) is None
    assert newsline_unavailable_reason(state, today=date(2026, 11, 5)) is None
    assert newsline_unavailable_reason(state, today=date(2026, 11, 6)) == "Newsline is not available"


def test_script_on_disk_supplies_friday_when_state_omits_it(tmp_path, monkeypatch):
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    folder = tmp_path / "2556"
    stories = folder / "stories"
    stories.mkdir(parents=True)
    (stories / "2556_01_story.mp3").write_bytes(b"mp3")
    (folder / "nsln2556.txt").write_text(HEADER_2556, encoding="utf-8")
    state = NewsState(report_number=2556, split_ok=True)
    assert newsline_unavailable_reason(state, today=date(2026, 10, 29)) is None
    assert newsline_unavailable_reason(state, today=date(2026, 10, 30)) == "Newsline is not available"


def test_missing_friday_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    stories = tmp_path / "2556" / "stories"
    stories.mkdir(parents=True)
    (stories / "2556_01_story.mp3").write_bytes(b"mp3")
    state = NewsState(report_number=2556, split_ok=True)
    assert newsline_unavailable_reason(state, today=date(2026, 10, 30)) == "Newsline is not available"


def test_failed_check_blocks_even_when_last_week_is_still_in_window(tmp_path, monkeypatch):
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    stories = tmp_path / "2556" / "stories"
    stories.mkdir(parents=True)
    (stories / "2556_01_story.mp3").write_bytes(b"mp3")
    state = NewsState(
        report_number=2556,
        split_ok=True,
        release_friday=date(2026, 10, 23),
        error="split failed",
    )
    assert newsline_unavailable_reason(state, today=date(2026, 10, 29)) == "Newsline is not available"
