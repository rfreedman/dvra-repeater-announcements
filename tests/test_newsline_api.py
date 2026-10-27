"""Newsline library, settings, and the manual check. No network and no Whisper."""

from __future__ import annotations

import threading

import pytest

from app.audio import PcmChunk
from app.models import Announcement
from app.newsline.poll import run_check
from app.newsline.preview import (
    PreviewError,
    frame_named_piece,
    frame_piece_pcm,
    latest_ready_stories,
    preview_chunks,
    preview_frames,
)
from app.newsline.state import NewsState, load_state, save_state


def test_newsline_item_requires_a_station_id_and_protects_it(client):
    station = client.post(
        "/api/announcements",
        json={"name": "Station ID", "text": "This is the station."},
    ).json()
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
            "station_id_announcement_id": station["id"],
            "ptt_gap_seconds": 2,
        },
    )
    assert created.status_code == 200
    assert created.json()["kind"] == "newsline"
    assert created.json()["text"] == ""
    removed = client.delete(f"/api/announcements/{station['id']}")
    assert removed.status_code == 409


def test_schedule_list_keeps_a_newsline_announcement(client):
    station = client.post(
        "/api/announcements",
        json={"name": "Station ID", "text": "This is the station."},
    ).json()
    news = client.post(
        "/api/announcements",
        json={
            "name": "News",
            "kind": "newsline",
            "station_id_announcement_id": station["id"],
        },
    ).json()
    created = client.post(
        "/api/schedules",
        json={
            "name": "Newsline Test",
            "kind": "weekly",
            "announcement_id": news["id"],
            "days": ["mon"],
            "slots": ["17:30"],
            "priority": 500,
        },
    )
    assert created.status_code == 200
    assert created.json()["announcement_id"] == news["id"]
    rows = client.get("/api/schedules").json()["schedules"]
    row = next(item for item in rows if item["name"] == "Newsline Test")
    assert row["announcement_id"] == news["id"]
    assert row["announcement_name"] == "News"


def test_newsline_download_times_round_trip(client):
    saved = client.put(
        "/api/settings",
        json={
            "news_check_start": "13:30",
            "news_check_stop": "17:00",
            "repeater_timeout_seconds": 200,
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["news_check_start"] == "13:30"
    assert body["news_check_stop"] == "17:00"
    assert body["repeater_timeout_seconds"] == 200
    again = client.get("/api/settings").json()
    assert again["news_check_start"] == "13:30"
    assert again["repeater_timeout_seconds"] == 200


def test_manual_check_runs_outside_the_window_without_the_network(monkeypatch, store, tmp_path):
    calls: dict[str, int] = {"feed": 0, "download": 0, "split": 0}

    class Report:
        report_number = 2554
        audio_url = "http://example.invalid/Report2554.mp3"

    monkeypatch.setattr("app.newsline.poll.ffmpeg_available", lambda: True)
    monkeypatch.setattr("app.newsline.poll.fetch_feed", lambda: calls.__setitem__("feed", calls["feed"] + 1))
    monkeypatch.setattr("app.newsline.poll.latest_report", lambda _feed: Report())

    def download(_url, dest):
        calls["download"] += 1
        dest.write_bytes(b"audio")

    def split(**_kwargs):
        calls["split"] += 1

    monkeypatch.setattr("app.newsline.poll.download_file", download)
    monkeypatch.setattr("app.newsline.poll.split_report", split)
    state = run_check(manual=True, root=tmp_path)
    assert calls == {"feed": 1, "download": 2, "split": 1}
    assert state.split_ok
    assert state.report_number == 2554
    assert (tmp_path / "2554" / "stories").is_dir() or state.error is None


def test_check_reports_download_and_processing_then_an_unchanged_report(monkeypatch, store, tmp_path):
    calls = {"download": 0, "split": 0}

    class Report:
        report_number = 2557
        audio_url = "http://example.invalid/Report2557.mp3"

    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    monkeypatch.setattr("app.newsline.poll.ffmpeg_available", lambda: True)
    monkeypatch.setattr("app.newsline.poll.fetch_feed", lambda: None)
    monkeypatch.setattr("app.newsline.poll.latest_report", lambda _feed: Report())

    def download(_url, dest):
        calls["download"] += 1
        current = load_state(tmp_path)
        assert current.phase == "downloading"
        assert current.working_report == 2557
        assert current.report_number is None
        dest.write_bytes(b"audio")

    def split(**_kwargs):
        calls["split"] += 1
        current = load_state(tmp_path)
        assert current.phase == "splitting"
        assert current.working_report == 2557

    monkeypatch.setattr("app.newsline.poll.download_file", download)
    monkeypatch.setattr("app.newsline.poll.split_report", split)
    monkeypatch.setattr("app.newsline.poll.check_in_progress", lambda: True)
    first = run_check(manual=True, root=tmp_path)
    assert calls == {"download": 2, "split": 1}
    assert first.last_outcome == "new"
    assert first.phase == "idle"
    assert first.working_report is None
    assert first.report_number == 2557
    monkeypatch.setattr("app.newsline.poll.check_in_progress", lambda: False)
    second = run_check(manual=True, root=tmp_path)
    assert calls["split"] == 1
    assert second.last_outcome == "current"
    assert second.report_number == 2557
    from app.newsline.poll import status_payload

    payload = status_payload()
    assert payload["phase"] == "idle"
    assert payload["outcome"] == "current"
    assert payload["working_report"] is None
    assert payload["in_progress"] is False


def test_status_keeps_the_real_phase_while_a_check_is_running(monkeypatch, tmp_path):
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    monkeypatch.setattr("app.newsline.poll.check_in_progress", lambda: True)
    save_state(
        NewsState(phase="downloading", working_report=2558, report_number=2556, split_ok=True),
        tmp_path,
    )
    from app.newsline.poll import status_payload

    payload = status_payload()
    assert payload["phase"] == "downloading"
    assert payload["working_report"] == 2558
    assert payload["in_progress"] is True


def _seed_report(tmp_path, number, *, files):
    stories = tmp_path / str(number) / "stories"
    stories.mkdir(parents=True)
    for name, body in files:
        (stories / name).write_bytes(body)
    return stories


def test_preview_warns_when_no_report_is_ready(client, monkeypatch, tmp_path):
    monkeypatch.setattr("app.newsline.preview.NEWSLINE_DIR", tmp_path)
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    res = client.post("/api/newsline/preview", json={})
    assert res.status_code == 400
    assert res.json()["detail"] == "No Newsline report is ready to play."


def test_preview_plays_the_latest_report_stories(client, store, monkeypatch, tmp_path):
    monkeypatch.setattr("app.newsline.preview.NEWSLINE_DIR", tmp_path)
    monkeypatch.setattr("app.newsline.state.NEWSLINE_DIR", tmp_path)
    monkeypatch.setattr("app.newsline.preview.ffmpeg_available", lambda: True)
    _seed_report(
        tmp_path,
        2555,
        files=[("2555_01.mp3", b"old")],
    )
    _seed_report(
        tmp_path,
        2556,
        files=[
            ("2556_01.mp3", b"new"),
            ("2556_05_station-id.mp3", b""),
            ("2556_06.mp3", b"end"),
        ],
    )
    save_state(NewsState(report_number=2556, split_ok=True), tmp_path)
    station = store.save_announcement(Announcement(name="ID", text="This is the station."))

    def fake_mp3(path, sample_rate=22050):
        return [PcmChunk(pcm_int16=path.stem[-2:].encode(), sample_rate=sample_rate)]

    def fake_tts(_announcement):
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


def test_latest_ready_stories_picks_the_highest_folder(tmp_path):
    _seed_report(tmp_path, 2554, files=[("2554_01.mp3", b"a")])
    _seed_report(tmp_path, 2556, files=[("2556_01.mp3", b"b")])
    stories = latest_ready_stories(tmp_path)
    assert stories is not None
    assert stories.parent.name == "2556"


def test_preview_chunks_raise_without_a_report(tmp_path):
    with pytest.raises(PreviewError, match="ready to play"):
        preview_chunks(root=tmp_path)


def test_frame_piece_pcm_prefixes_length_and_has_no_trailing_gap():
    framed = frame_piece_pcm(b"ab") + frame_piece_pcm(b"cd")
    assert framed[:4] == b"\x00\x00\x00\x02"
    assert framed[4:6] == b"ab"
    assert framed[6:10] == b"\x00\x00\x00\x02"
    assert framed[10:] == b"cd"


def test_frame_named_piece_prefixes_utf8_name_then_pcm():
    framed = frame_named_piece("intro.mp3", b"ab")
    assert framed[:4] == b"\x00\x00\x00\x09"
    assert framed[4:13] == b"intro.mp3"
    assert framed[13:17] == b"\x00\x00\x00\x02"
    assert framed[17:] == b"ab"


def test_preview_frames_are_length_prefixed(store, monkeypatch, tmp_path):
    monkeypatch.setattr("app.newsline.preview.ffmpeg_available", lambda: True)
    _seed_report(tmp_path, 2556, files=[("2556_01.mp3", b"new"), ("2556_02.mp3", b"end")])
    save_state(NewsState(report_number=2556, split_ok=True), tmp_path)
    monkeypatch.setattr(
        "app.newsline.preview.mp3_to_chunks",
        lambda path, sample_rate=22050: [PcmChunk(pcm_int16=path.stem[-2:].encode(), sample_rate=sample_rate)],
    )
    _rate, frames = preview_frames(root=tmp_path)
    body = b"".join(frames)
    assert body == frame_named_piece("2556_01.mp3", b"01") + frame_named_piece("2556_02.mp3", b"02")


def test_manual_check_refuses_a_second_run(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake(*, manual, root=None):
        started.set()
        release.wait(5)
        return NewsState()

    monkeypatch.setattr("app.newsline.poll.run_check", fake)
    monkeypatch.setattr("app.newsline.poll.load_state", lambda root=None: NewsState())
    monkeypatch.setattr("app.newsline.poll.save_state", lambda state, root=None: None)
    first = client.post("/api/newsline/check")
    assert first.status_code == 200
    assert first.json()["result"] == "started"
    assert started.wait(2)
    second = client.post("/api/newsline/check")
    assert second.status_code == 409
    release.set()
