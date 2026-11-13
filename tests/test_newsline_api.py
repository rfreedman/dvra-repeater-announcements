"""Newsline library, settings, and the manual check. No network and no Whisper."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Protocol, cast

import pytest
from fastapi.testclient import TestClient

from app.audio import PcmChunk
from app.models import Announcement, TtsAnnouncement
from app.newsline.poll import run_check
from app.newsline.split_job import SplitError
from app.store import AnnouncementStore
from app.newsline.preview import (
    PreviewError,
    frame_named_piece,
    frame_piece_pcm,
    latest_ready_stories,
    preview_chunks,
    preview_frames,
)
from app.newsline.state import NewsState, load_state, save_state
from tests.jsonutil import json_dict


class _SplitStub(Protocol):
    def __call__(self, **kwargs: object) -> None: ...


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise AssertionError("expected a list of JSON objects")
    items = cast(list[object], value)
    if not all(isinstance(item, dict) for item in items):
        raise AssertionError("expected a list of JSON objects")
    return cast(list[dict[str, object]], items)


def test_newsline_item_requires_a_station_id_and_protects_it(client: TestClient) -> None:
    station = json_dict(client.post(
        "/api/announcements",
        json={"name": "Station ID", "text": "This is the station."},
    ))
    station_id = station["id"]
    assert isinstance(station_id, str)
    missing = client.post(
        "/api/announcements",
        json={"name": "Newsline", "kind": "newsline"},
    )
    assert missing.status_code == 422
    created = client.post(
        "/api/announcements",
        json={
            "name": "Newsline",
            "kind": "newsline",
            "station_id_announcement_id": station_id,
            "resume_announcement_id": station_id,
            "ptt_gap_seconds": 2,
        },
    )
    assert created.status_code == 200
    body = json_dict(created)
    assert body["kind"] == "newsline"
    assert "text" not in body
    assert body["resume_announcement_id"] == station_id
    removed = client.delete(f"/api/announcements/{station_id}")
    assert removed.status_code == 409


def test_schedule_list_keeps_a_newsline_announcement(client: TestClient) -> None:
    station = json_dict(client.post(
        "/api/announcements",
        json={"name": "Station ID", "text": "This is the station."},
    ))
    station_id = station["id"]
    assert isinstance(station_id, str)
    news = json_dict(client.post(
        "/api/announcements",
        json={
            "name": "News",
            "kind": "newsline",
            "station_id_announcement_id": station_id,
        },
    ))
    news_id = news["id"]
    assert isinstance(news_id, str)
    created = client.post(
        "/api/schedules",
        json={
            "name": "Newsline Test",
            "kind": "weekly",
            "announcement_id": news_id,
            "days": ["mon"],
            "slots": ["17:30"],
            "priority": 500,
        },
    )
    assert created.status_code == 200
    assert json_dict(created)["announcement_id"] == news_id
    rows = _dict_list(json_dict(client.get("/api/schedules"))["schedules"])
    row = next(item for item in rows if item["name"] == "Newsline Test")
    assert row["announcement_id"] == news_id
    assert row["announcement_name"] == "News"


def test_settings_save_leaves_newsline_schedule_in_config(client: TestClient) -> None:
    saved = client.put(
        "/api/settings",
        json={
            "news_check_start": "13:30",
            "news_check_stop": "17:00",
            "repeater_timeout_seconds": 200,
            "baseline_randomize": True,
        },
    )
    assert saved.status_code == 200
    body = json_dict(saved)
    assert "news_check_start" not in body
    assert "repeater_timeout_seconds" not in body
    assert body["baseline_randomize"] is True
    home = json_dict(client.get("/api/schedules"))["settings"]
    assert isinstance(home, dict)
    assert home["news_check_start"] == "12:00"
    assert home["news_check_stop"] == "18:00"
    assert home["news_check_interval_hours"] == 1
    assert home["repeater_timeout_seconds"] == 180


def test_manual_check_runs_outside_the_window_without_the_network(monkeypatch: pytest.MonkeyPatch, store: AnnouncementStore, tmp_path: Path) -> None:
    _ = store
    calls: dict[str, int] = {"feed": 0, "download": 0, "split": 0}

    class Report:
        report_number: int = 2554
        audio_url: str = "http://example.invalid/Report2554.mp3"
        script_url: str = "http://example.invalid/nsln2554.txt"

    def ffmpeg_available() -> bool:
        return True

    def fetch_feed() -> None:
        calls["feed"] += 1

    def latest_report(_feed: object) -> Report:
        return Report()

    def download(_url: str, dest: Path) -> None:
        calls["download"] += 1
        if dest.suffix == ".txt":
            _ = dest.write_text(
                "Amateur Radio Newsline Report 2554 for Friday, October 9th, 2026\n",
                encoding="utf-8",
            )
        else:
            _ = dest.write_bytes(b"audio")

    def split(**_kwargs: object) -> None:
        calls["split"] += 1

    monkeypatch.setattr("app.newsline.poll.ffmpeg_available", ffmpeg_available)
    monkeypatch.setattr("app.newsline.poll.fetch_feed", fetch_feed)
    monkeypatch.setattr("app.newsline.poll.latest_report", latest_report)
    monkeypatch.setattr("app.newsline.poll.download_file", download)
    monkeypatch.setattr("app.newsline.poll.split_report", split)
    state = run_check(manual=True, root=tmp_path)
    assert calls == {"feed": 1, "download": 2, "split": 1}
    assert state.split_ok
    assert state.report_number == 2554
    assert state.release_friday == date(2026, 10, 9)
    assert (tmp_path / "2554" / "stories").is_dir() or state.error is None


def test_check_reports_download_and_processing_then_an_unchanged_report(monkeypatch: pytest.MonkeyPatch, store: AnnouncementStore, tmp_path: Path) -> None:
    _ = store
    calls = {"download": 0, "split": 0}

    class Report:
        report_number: int = 2557
        audio_url: str = "http://example.invalid/Report2557.mp3"
        script_url: str = "http://example.invalid/NSLN2557.txt"
        published_on: datetime | None = None

    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)

    def ffmpeg_available() -> bool:
        return True

    def fetch_feed() -> None:
        return None

    def latest_report(_feed: object) -> Report:
        return Report()

    def download(_url: str, dest: Path) -> None:
        calls["download"] += 1
        current = load_state(tmp_path)
        assert current.phase == "downloading"
        assert current.working_report == 2557
        assert current.report_number is None
        if dest.suffix == ".txt":
            _ = dest.write_text(
                "Amateur Radio Newsline Report 2557 for Friday, October 30th, 2026\n",
                encoding="utf-8",
            )
        else:
            _ = dest.write_bytes(b"audio")

    def split(**_kwargs: object) -> None:
        calls["split"] += 1
        current = load_state(tmp_path)
        assert current.phase == "splitting"
        assert current.working_report == 2557
        from app.newsline.poll import status_payload

        payload = status_payload()
        assert payload["step"] == "Processing"
        assert payload["elapsed_seconds"] is not None
        assert payload["elapsed_seconds"] >= 0

    monkeypatch.setattr("app.newsline.poll.ffmpeg_available", ffmpeg_available)
    monkeypatch.setattr("app.newsline.poll.fetch_feed", fetch_feed)
    monkeypatch.setattr("app.newsline.poll.latest_report", latest_report)
    monkeypatch.setattr("app.newsline.poll.download_file", download)
    monkeypatch.setattr("app.newsline.poll.split_report", split)
    def check_in_progress() -> bool:
        return True

    monkeypatch.setattr("app.newsline.poll.check_in_progress", check_in_progress)
    first = run_check(manual=True, root=tmp_path)
    assert calls == {"download": 2, "split": 1}
    assert first.last_outcome == "new"
    assert first.phase == "idle"
    assert first.working_report is None
    assert first.report_number == 2557
    assert first.release_friday == date(2026, 10, 30)
    assert first.published_on is None
    Report.published_on = datetime(2026, 10, 29, 16, 5, tzinfo=ZoneInfo("America/New_York"))
    def check_finished() -> bool:
        return False

    monkeypatch.setattr("app.newsline.poll.check_in_progress", check_finished)
    second = run_check(manual=True, root=tmp_path)
    assert calls["split"] == 1
    assert second.last_outcome == "current"
    assert second.report_number == 2557
    assert second.published_on == datetime(2026, 10, 29, 16, 5, tzinfo=ZoneInfo("America/New_York"))
    from app.newsline.poll import status_payload

    payload = status_payload()
    assert payload["phase"] == "idle"
    assert payload["outcome"] == "current"
    assert payload["working_report"] is None
    assert payload["in_progress"] is False
    assert payload["step"] is None
    assert payload["elapsed_seconds"] is None
    assert payload["release_friday"] == "2026-10-30"
    assert payload["published_on"] == "2026-10-29T16:05:00-04:00"


def test_status_keeps_the_real_phase_while_a_check_is_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    def check_in_progress() -> bool:
        return True

    monkeypatch.setattr("app.newsline.poll.check_in_progress", check_in_progress)
    save_state(
        NewsState(phase="downloading", working_report=2558, report_number=2556, split_ok=True),
        tmp_path,
    )
    from app.newsline.poll import status_payload

    payload = status_payload()
    assert payload["phase"] == "downloading"
    assert payload["working_report"] == 2558
    assert payload["in_progress"] is True
    assert payload["release_friday"] is None


def _seed_report(
    tmp_path: Path, number: int, *, files: Iterable[tuple[str, bytes]]
) -> Path:
    stories = tmp_path / str(number) / "stories"
    stories.mkdir(parents=True)
    for name, body in files:
        _ = (stories / name).write_bytes(body)
    return stories


def test_preview_warns_when_no_report_is_ready(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.newsline.preview.NEWSLINE_DIR", tmp_path)
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    res = client.post("/api/newsline/preview", json={})
    assert res.status_code == 400
    assert json_dict(res)["detail"] == "No Newsline report is ready to play."


def test_preview_plays_the_latest_report_stories(client: TestClient, store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.newsline.preview.NEWSLINE_DIR", tmp_path)
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    def ffmpeg_available() -> bool:
        return True

    monkeypatch.setattr("app.newsline.preview.ffmpeg_available", ffmpeg_available)
    _ = _seed_report(
        tmp_path,
        2555,
        files=[("2555_01.mp3", b"old")],
    )
    _ = _seed_report(
        tmp_path,
        2556,
        files=[
            ("2556_01.mp3", b"new"),
            ("2556_05_station-id.mp3", b""),
            ("2556_06.mp3", b"end"),
        ],
    )
    save_state(NewsState(report_number=2556, split_ok=True), tmp_path)
    station = store.save_announcement(TtsAnnouncement(name="ID", text="This is the station."))

    def fake_mp3(path: Path, sample_rate: int = 22050) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=path.stem[-2:].encode(), sample_rate=sample_rate)]

    def fake_tts(_announcement: Announcement) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=b"ID", sample_rate=22050)]

    monkeypatch.setattr("app.newsline.preview.mp3_to_chunks", fake_mp3)
    monkeypatch.setattr("app.pcm_cache.chunks_for_announcement", fake_tts)
    res = client.post(
        "/api/newsline/preview",
        json={"station_id_announcement_id": station.id},
    )
    assert res.status_code == 200
    assert res.headers.get("x-preview-framing") == "name32+length32"
    expected = (
        frame_named_piece("2556_01.mp3", b"01")
        + frame_named_piece("ID", b"ID")
        + frame_named_piece("2556_06.mp3", b"06")
    )
    assert res.content == expected
    assert b"\x00" * 100 not in res.content


def test_latest_ready_stories_picks_the_highest_folder(tmp_path: Path) -> None:
    _ = _seed_report(tmp_path, 2554, files=[("2554_01.mp3", b"a")])
    _ = _seed_report(tmp_path, 2556, files=[("2556_01.mp3", b"b")])
    stories = latest_ready_stories(tmp_path)
    assert stories is not None
    assert stories.parent.name == "2556"


def test_preview_chunks_raise_without_a_report(tmp_path: Path) -> None:
    with pytest.raises(PreviewError, match="ready to play"):
        _ = preview_chunks(root=tmp_path)


def test_frame_piece_pcm_prefixes_length_and_has_no_trailing_gap() -> None:
    framed = frame_piece_pcm(b"ab") + frame_piece_pcm(b"cd")
    assert framed[:4] == b"\x00\x00\x00\x02"
    assert framed[4:6] == b"ab"
    assert framed[6:10] == b"\x00\x00\x00\x02"
    assert framed[10:] == b"cd"


def test_frame_named_piece_prefixes_utf8_name_then_pcm() -> None:
    framed = frame_named_piece("intro.mp3", b"ab")
    assert framed[:4] == b"\x00\x00\x00\x09"
    assert framed[4:13] == b"intro.mp3"
    assert framed[13:17] == b"\x00\x00\x00\x02"
    assert framed[17:] == b"ab"


def test_preview_frames_are_length_prefixed(store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _ = store
    def ffmpeg_available() -> bool:
        return True

    def fake_mp3(path: Path, sample_rate: int = 22050) -> list[PcmChunk]:
        return [
            PcmChunk(
                pcm_int16=path.stem[-2:].encode(), sample_rate=sample_rate
            )
        ]

    monkeypatch.setattr("app.newsline.preview.ffmpeg_available", ffmpeg_available)
    _ = _seed_report(tmp_path, 2556, files=[("2556_01.mp3", b"new"), ("2556_02.mp3", b"end")])
    save_state(NewsState(report_number=2556, split_ok=True), tmp_path)
    monkeypatch.setattr("app.newsline.preview.mp3_to_chunks", fake_mp3)
    _rate, frames = preview_frames(root=tmp_path)
    body = b"".join(frames)
    assert body == frame_named_piece("2556_01.mp3", b"01") + frame_named_piece("2556_02.mp3", b"02")


def test_preview_resamples_16khz_tts_opening_to_stream_rate(
    store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Kathleen (and other low Piper voices) is 16 kHz; preview advertises 22050."""
    import struct

    from app.newsline.decode import SAMPLE_RATE

    monkeypatch.setattr("app.newsline.preview.NEWSLINE_DIR", tmp_path)
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)

    def ffmpeg_available() -> bool:
        return True

    monkeypatch.setattr("app.newsline.preview.ffmpeg_available", ffmpeg_available)
    _ = _seed_report(tmp_path, 2556, files=[("2556_01.mp3", b"story")])
    save_state(NewsState(report_number=2556, split_ok=True), tmp_path)
    opening = store.save_announcement(TtsAnnouncement(name="Open", text="Welcome to newsline."))

    # One second of mono int16 at Kathleen's native rate.
    pcm_16k = b"\x00\x01" * 16000

    def fake_mp3(_path: Path, sample_rate: int = 22050) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=b"ST", sample_rate=sample_rate)]

    def fake_tts(_announcement: Announcement) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=pcm_16k, sample_rate=16000)]

    monkeypatch.setattr("app.newsline.preview.mp3_to_chunks", fake_mp3)
    monkeypatch.setattr("app.pcm_cache.chunks_for_announcement", fake_tts)

    rate, frames = preview_frames(
        opening_announcement_id=opening.id,
        root=tmp_path,
    )
    assert rate == SAMPLE_RATE
    body = b"".join(frames)
    name_len = struct.unpack(">I", body[:4])[0]
    pcm_start = 4 + name_len
    pcm_len = struct.unpack(">I", body[pcm_start : pcm_start + 4])[0]
    # 16000 samples @ 16 kHz → ~22050 samples @ preview rate (2 bytes each).
    assert pcm_len == SAMPLE_RATE * 2


def test_manual_check_refuses_a_second_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def fake(*, manual: bool, root: Path | None = None) -> NewsState:
        _ = manual
        _ = root
        started.set()
        _ = release.wait(5)
        return NewsState()

    monkeypatch.setattr("app.newsline.poll.run_check", fake)
    def fake_load_state(root: Path | None = None) -> NewsState:
        _ = root
        return NewsState()

    def fake_save_state(state: NewsState, root: Path | None = None) -> None:
        _ = state
        _ = root

    monkeypatch.setattr("app.newsline.poll.load_state", fake_load_state)
    monkeypatch.setattr("app.newsline.poll.save_state", fake_save_state)
    first = client.post("/api/newsline/check")
    assert first.status_code == 200
    assert json_dict(first)["result"] == "started"
    assert started.wait(2)
    second = client.post("/api/newsline/check")
    assert second.status_code == 409
    assert json_dict(second)["detail"] == "A Newsline check is already running"
    release.set()


def test_manual_check_refuses_while_newsline_is_playing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsline.session import bulletin_session

    def start_manual_check() -> bool:
        return True

    monkeypatch.setattr("app.newsline.poll.start_manual_check", start_manual_check)
    bulletin_session.begin()
    try:
        res = client.post("/api/newsline/check")
        assert res.status_code == 409
        detail = json_dict(res)["detail"]
        assert isinstance(detail, str)
        assert "playing" in detail.casefold()
    finally:
        bulletin_session.end()


def test_status_payload_includes_playing_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.newsline.poll import status_payload
    from app.newsline.session import bulletin_session

    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    save_state(NewsState(), tmp_path)
    assert status_payload()["playing"] is False
    bulletin_session.begin()
    try:
        assert status_payload()["playing"] is True
    finally:
        bulletin_session.end()


def test_failed_check_is_unavailable_even_when_last_week_files_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsline.playlist import newsline_unavailable_reason, stories_ready

    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    stories = tmp_path / "2556" / "stories"
    stories.mkdir(parents=True)
    _ = (stories / "2556_01_story.mp3").write_bytes(b"mp3")
    state = NewsState(report_number=2556, split_ok=True, error="split failed")
    assert stories_ready(state.stories_dir(tmp_path))
    assert newsline_unavailable_reason(state) == "Newsline is not available"


def test_ready_report_is_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsline.playlist import newsline_unavailable_reason

    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    stories = tmp_path / "2557" / "stories"
    stories.mkdir(parents=True)
    _ = (stories / "2557_01_story.mp3").write_bytes(b"mp3")
    state = NewsState(
        report_number=2557,
        split_ok=True,
        error=None,
        release_friday=date(2026, 10, 30),
    )
    assert newsline_unavailable_reason(state, today=date(2026, 10, 30)) is None


def _plant_cache(root: Path, number: int) -> tuple[Path, Path]:
    align = root / "cache" / "forced_align"
    slices = root / "cache" / "slices"
    align.mkdir(parents=True, exist_ok=True)
    slices.mkdir(parents=True, exist_ok=True)
    align_file = align / f"Report{number}_base_en_abc.json"
    slice_file = slices / f"{number:04d}_01_story_small_en_qa.json"
    _ = align_file.write_text("{}", encoding="utf-8")
    _ = slice_file.write_text("{}", encoding="utf-8")
    return align_file, slice_file


def _script_for(number: int, friday: str) -> str:
    return f"Amateur Radio Newsline Report {number} for Friday, {friday}\n"


def _stub_feed(
    monkeypatch: pytest.MonkeyPatch,
    number: int,
    *,
    split: _SplitStub | None = None,
    download: Callable[[str, Path], None] | None = None,
) -> None:
    class Report:
        report_number: int = number
        audio_url: str = f"http://example.invalid/Report{number}.mp3"
        script_url: str = f"http://example.invalid/nsln{number}.txt"

    def ffmpeg_available() -> bool:
        return True

    def fetch_feed() -> None:
        return None

    def latest_report(_feed: object) -> Report:
        return Report()

    monkeypatch.setattr("app.newsline.poll.ffmpeg_available", ffmpeg_available)
    monkeypatch.setattr("app.newsline.poll.fetch_feed", fetch_feed)
    monkeypatch.setattr("app.newsline.poll.latest_report", latest_report)
    if download is not None:
        monkeypatch.setattr("app.newsline.poll.download_file", download)
    if split is not None:
        monkeypatch.setattr("app.newsline.poll.split_report", split)


def _write_download(_url: str, dest: Path, number: int, friday: str) -> None:
    if dest.suffix == ".txt":
        _ = dest.write_text(_script_for(number, friday), encoding="utf-8")
    else:
        _ = dest.write_bytes(b"audio")


def test_failed_split_is_not_retried_on_the_next_scheduled_check(monkeypatch: pytest.MonkeyPatch, store: AnnouncementStore, tmp_path: Path) -> None:
    _ = store
    calls = {"download": 0, "split": 0}
    last_week = NewsState(report_number=2556, split_ok=True, release_friday=date(2026, 10, 23))
    save_state(last_week, tmp_path)
    kept_align, kept_slice = _plant_cache(tmp_path, 2556)

    def download(_url: str, dest: Path) -> None:
        calls["download"] += 1
        _write_download(_url, dest, 2557, "October 30th, 2026")

    def split(**_kwargs: object) -> None:
        calls["split"] += 1
        _ = _plant_cache(tmp_path, 2557)
        raise SplitError("align failed")

    _stub_feed(monkeypatch, 2557, download=download, split=split)
    first = run_check(manual=False, root=tmp_path)
    assert first.split_failed_report == 2557
    assert first.report_number == 2556
    assert first.split_ok
    assert first.error == "align failed"
    assert calls == {"download": 2, "split": 1}
    assert not (tmp_path / "cache" / "forced_align" / "Report2557_base_en_abc.json").exists()
    assert not (tmp_path / "cache" / "slices" / "2557_01_story_small_en_qa.json").exists()
    assert kept_align.is_file()
    assert kept_slice.is_file()

    second = run_check(manual=False, root=tmp_path)
    assert calls == {"download": 2, "split": 1}
    assert second.split_failed_report == 2557
    assert second.report_number == 2556
    assert second.error == "align failed"


def test_manual_check_redownloads_after_a_failed_split(monkeypatch: pytest.MonkeyPatch, store: AnnouncementStore, tmp_path: Path) -> None:
    _ = store
    calls = {"download": 0, "split": 0}
    save_state(
        NewsState(
            report_number=2556,
            split_ok=True,
            split_failed_report=2557,
            error="align failed",
            release_friday=date(2026, 10, 23),
        ),
        tmp_path,
    )
    folder = tmp_path / "2557"
    folder.mkdir(parents=True)
    _ = (folder / "Report2557.mp3").write_bytes(b"stale")
    _ = (folder / "nsln2557.txt").write_text("stale", encoding="utf-8")
    (folder / "stories").mkdir()
    _ = (folder / "stories" / "2557_01.mp3").write_bytes(b"stale")
    failed_align, failed_slice = _plant_cache(tmp_path, 2557)
    kept_align, kept_slice = _plant_cache(tmp_path, 2556)

    def download(_url: str, dest: Path) -> None:
        calls["download"] += 1
        assert not dest.exists()
        _write_download(_url, dest, 2557, "October 30th, 2026")

    def split(**_kwargs: object) -> None:
        calls["split"] += 1
        _ = _plant_cache(tmp_path, 2557)

    _stub_feed(monkeypatch, 2557, download=download, split=split)
    state = run_check(manual=True, root=tmp_path)
    assert calls == {"download": 2, "split": 1}
    assert state.split_ok
    assert state.report_number == 2557
    assert state.split_failed_report is None
    assert state.error is None
    assert not failed_align.exists()
    assert not failed_slice.exists()
    assert kept_align.is_file()
    assert kept_slice.is_file()
    assert (tmp_path / "2557" / "Report2557.mp3").read_bytes() == b"audio"


def test_newer_report_downloads_after_a_failed_split(monkeypatch: pytest.MonkeyPatch, store: AnnouncementStore, tmp_path: Path) -> None:
    _ = store
    calls = {"download": 0, "split": 0}
    save_state(
        NewsState(
            report_number=2556,
            split_ok=True,
            split_failed_report=2557,
            error="align failed",
            release_friday=date(2026, 10, 23),
        ),
        tmp_path,
    )
    _ = _plant_cache(tmp_path, 2557)

    def download(_url: str, dest: Path) -> None:
        calls["download"] += 1
        _write_download(_url, dest, 2558, "November 6th, 2026")

    def split(**_kwargs: object) -> None:
        calls["split"] += 1

    _stub_feed(monkeypatch, 2558, download=download, split=split)
    state = run_check(manual=False, root=tmp_path)
    assert calls == {"download": 2, "split": 1}
    assert state.report_number == 2558
    assert state.split_failed_report is None
    assert state.error is None
    assert not (tmp_path / "cache" / "forced_align" / "Report2557_base_en_abc.json").exists()
    assert not (tmp_path / "cache" / "slices" / "2557_01_story_small_en_qa.json").exists()


def test_successful_split_clears_that_report_cache(monkeypatch: pytest.MonkeyPatch, store: AnnouncementStore, tmp_path: Path) -> None:
    _ = store
    kept_align, kept_slice = _plant_cache(tmp_path, 2556)

    def download(_url: str, dest: Path) -> None:
        _write_download(_url, dest, 2557, "October 30th, 2026")

    def split(**_kwargs: object) -> None:
        _ = _plant_cache(tmp_path, 2557)

    _stub_feed(monkeypatch, 2557, download=download, split=split)
    state = run_check(manual=True, root=tmp_path)
    assert state.split_ok
    assert state.report_number == 2557
    assert not (tmp_path / "cache" / "forced_align" / "Report2557_base_en_abc.json").exists()
    assert not (tmp_path / "cache" / "slices" / "2557_01_story_small_en_qa.json").exists()
    assert kept_align.is_file()
    assert kept_slice.is_file()


def test_download_failure_is_retried_on_the_next_scheduled_check(monkeypatch: pytest.MonkeyPatch, store: AnnouncementStore, tmp_path: Path) -> None:
    _ = store
    calls = {"download": 0, "split": 0}

    def download(_url: str, dest: Path) -> None:
        _ = dest
        calls["download"] += 1
        raise OSError("network down")

    def split(**_kwargs: object) -> None:
        calls["split"] += 1

    _stub_feed(monkeypatch, 2557, download=download, split=split)
    first = run_check(manual=False, root=tmp_path)
    assert first.split_failed_report is None
    assert first.error == "network down"
    assert calls == {"download": 1, "split": 0}

    second = run_check(manual=False, root=tmp_path)
    assert calls["download"] == 2
    assert calls["split"] == 0
    assert second.split_failed_report is None


def test_missing_stories_are_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsline.playlist import newsline_unavailable_reason

    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    state = NewsState(report_number=2557, split_ok=True, error=None)
    assert newsline_unavailable_reason(state) == "Newsline is not available"
