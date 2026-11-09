from __future__ import annotations

import tomllib
from datetime import date, datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.audio import PcmChunk
from app.models import (
    Announcement,
    BaselineSchedule,
    TtsAnnouncement,
    validate_schedule,
)
from app.radio import StubRadio, set_radio
from app.store import AnnouncementStore, DataFileError
from tests.jsonutil import json_dict


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError("expected JSON object")
    return cast(dict[str, object], value)


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise AssertionError("expected a list of JSON objects")
    items = cast(list[object], value)
    if not all(isinstance(item, dict) for item in items):
        raise AssertionError("expected a list of JSON objects")
    return cast(list[dict[str, object]], items)


def _toml(text: str) -> dict[str, object]:
    body: object = tomllib.loads(text)
    return _dict(body)


def seed_baseline(
    store: AnnouncementStore, name: str = "Station ID"
) -> tuple[TtsAnnouncement, BaselineSchedule]:
    announcement = store.save_announcement(TtsAnnouncement(name=name, text="This is the ID."))
    assert isinstance(announcement, TtsAnnouncement)
    schedule = store.save_schedule(
        BaselineSchedule(name="Baseline ID", announcement_id=announcement.id)
    )
    assert isinstance(schedule, BaselineSchedule)
    return announcement, schedule


def test_unversioned_json_is_refused_not_overwritten(store: AnnouncementStore) -> None:
    legacy = '{"announcements": [{"id": "old", "name": "Legacy", "text": "gone", "schedules": []}]}\n'
    _ = store.path.write_text(legacy, encoding="utf-8")
    with pytest.raises(DataFileError):
        _ = store.list_announcements()
    with pytest.raises(DataFileError):
        _ = store.save_announcement(TtsAnnouncement(name="New", text="Hello"))
    assert store.path.read_text(encoding="utf-8") == legacy


def test_empty_json_file_is_an_empty_store(store: AnnouncementStore) -> None:
    _ = store.path.write_text("{}\n", encoding="utf-8")
    assert store.list_announcements() == []


def test_empty_store_lists_nothing(client: TestClient, store: AnnouncementStore) -> None:
    _ = store
    payload = json_dict(client.get("/api/schedules"))
    assert payload["schedules"] == []
    assert payload["upcoming"] is None
    assert payload["clock"]
    warnings = payload["warnings"]
    assert isinstance(warnings, list)
    warning_values = cast(list[object], warnings)
    assert any(
        isinstance(item, str) and "baseline" in item.lower()
        for item in warning_values
    )

def test_create_announcement_and_baseline(client: TestClient, store: AnnouncementStore) -> None:
    _ = store
    created = client.post(
        "/api/announcements",
        json={"name": "Station ID", "text": "This is w2-zee-q."},
    )
    assert created.status_code == 200
    created_body = json_dict(created)
    announcement_id = created_body["id"]
    assert isinstance(announcement_id, str)
    assert "schedules" not in created_body
    scheduled = client.post(
        "/api/schedules",
        json={
            "name": "Baseline ID",
            "kind": "baseline",
            "announcement_id": announcement_id,
        },
    )
    assert scheduled.status_code == 200
    listed = json_dict(client.get("/api/schedules"))
    schedules = _dict_list(listed["schedules"])
    upcoming = _dict(listed["upcoming"])
    clock = _dict_list(listed["clock"])
    assert schedules[0]["kind"] == "baseline"
    assert upcoming["announcement_name"] == "Station ID"
    assert clock[0]["source"] == "baseline"


def test_same_text_can_be_baseline_and_overlay(client: TestClient, store: AnnouncementStore) -> None:
    announcement, _baseline = seed_baseline(store)
    overlay = client.post(
        "/api/schedules",
        json={
            "name": "Weekly",
            "kind": "weekly",
            "announcement_id": announcement.id,
            "days": ["sun"],
            "slots": ["21:00"],
            "offset_minutes": -5,
        },
    )
    assert overlay.status_code == 200
    listed = _dict_list(json_dict(client.get("/api/schedules"))["schedules"])
    assert len(listed) == 2
    assert {row["kind"] for row in listed} == {"baseline", "weekly"}


def test_home_when_column_collapses_contiguous_slots_and_days(client: TestClient, store: AnnouncementStore) -> None:
    announcement, _baseline = seed_baseline(store)
    created = client.post(
        "/api/schedules",
        json={
            "name": "Drive time",
            "kind": "weekly",
            "announcement_id": announcement.id,
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "slots": ["07:00", "07:30", "08:00", "17:00"],
        },
    )
    assert created.status_code == 200
    rows = _dict_list(json_dict(client.get("/api/schedules"))["schedules"])
    drive = next(row for row in rows if row["name"] == "Drive time")
    assert drive["summary"] == "the 07:00–08:00 slots and the 17:00 slot on Monday–Friday"


def test_home_when_column_says_every_day(client: TestClient, store: AnnouncementStore) -> None:
    announcement, _baseline = seed_baseline(store)
    created = client.post(
        "/api/schedules",
        json={
            "name": "Daily ID extra",
            "kind": "weekly",
            "announcement_id": announcement.id,
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "slots": ["12:00"],
            "priority": 40,
        },
    )
    assert created.status_code == 200
    rows = _dict_list(json_dict(client.get("/api/schedules"))["schedules"])
    daily = next(row for row in rows if row["name"] == "Daily ID extra")
    assert daily["summary"] == "the 12:00 slot every day"


def test_duplicate_baseline_announcement_is_conflict(client: TestClient, store: AnnouncementStore) -> None:
    announcement, _baseline = seed_baseline(store)
    res = client.post(
        "/api/schedules",
        json={
            "name": "Also baseline",
            "kind": "baseline",
            "announcement_id": announcement.id,
        },
    )
    assert res.status_code == 409
    assert "already in the baseline pool" in str(json_dict(res)["detail"])


def test_equal_priority_overlays_conflict(client: TestClient, store: AnnouncementStore) -> None:
    announcement, _baseline = seed_baseline(store)
    extra = store.save_announcement(TtsAnnouncement(name="Other", text="Other text."))
    first = client.post(
        "/api/schedules",
        json={
            "name": "Net A",
            "kind": "weekly",
            "announcement_id": announcement.id,
            "days": ["sun"],
            "slots": ["21:00"],
        },
    )
    assert first.status_code == 200
    second = client.post(
        "/api/schedules",
        json={
            "name": "Net B",
            "kind": "weekly",
            "announcement_id": extra.id,
            "days": ["sun"],
            "slots": ["21:00"],
        },
    )
    assert second.status_code == 409
    assert "Conflicts with Net A" in str(json_dict(second)["detail"])


def test_cannot_delete_announcement_in_use(client: TestClient, store: AnnouncementStore) -> None:
    announcement, _baseline = seed_baseline(store)
    res = client.delete(f"/api/announcements/{announcement.id}")
    assert res.status_code == 409
    deleted = client.delete(f"/api/schedules/{_baseline.id}")
    assert deleted.status_code == 200
    gone = client.delete(f"/api/announcements/{announcement.id}")
    assert gone.status_code == 200


def test_offset_outside_window_rejected(client: TestClient, store: AnnouncementStore) -> None:
    announcement, _baseline = seed_baseline(store)
    res = client.post(
        "/api/schedules",
        json={
            "name": "Too early",
            "kind": "weekly",
            "announcement_id": announcement.id,
            "days": ["sun"],
            "slots": ["21:00"],
            "offset_minutes": -20,
        },
    )
    assert res.status_code == 400
    assert "±10" in str(json_dict(res)["detail"])


def test_speak_does_not_key_ptt(client: TestClient, store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = store
    radio = StubRadio()
    set_radio(radio)

    pcm = b"\x00\x00" * 2205  # 0.1s at 22050 Hz

    def fake_synthesize(
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        sentence_pause: float = 0.25,
    ) -> tuple[str, int, list[PcmChunk]]:
        _ = text
        _ = voice
        _ = speed
        _ = sentence_pause
        return "en_US-ryan-medium", 22050, [PcmChunk(pcm_int16=pcm, sample_rate=22050)]

    monkeypatch.setattr("app.routes.speak.registry.synthesize", fake_synthesize)
    res = client.post("/api/speak", json={"text": "hello from preview"})
    assert res.status_code == 200
    assert res.content == pcm
    assert float(res.headers["X-Play-Seconds"]) == pytest.approx(0.1)
    assert not any(event[0] == "ptt" for event in radio.events)
    set_radio(None)


def test_upcoming_is_running_when_a_fire_is_in_progress(client: TestClient, store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = seed_baseline(store)
    def fake_running() -> dict[str, object]:
        return {
            "announcement_id": "ann-live",
            "announcement_name": "On-the-hour",
            "schedule_id": "sched-live",
            "started_at": "2026-08-28T12:00:00-04:00",
        }

    monkeypatch.setattr("app.home_live.get_running", fake_running)
    payload = json_dict(client.get("/api/schedules"))
    upcoming = _dict(payload["upcoming"])
    assert upcoming["status"] == "running"
    assert upcoming["announcement_name"] == "On-the-hour"
    assert upcoming["at"] == "2026-08-28T12:00:00-04:00"


def test_weekly_overlay_shows_on_sunday_clock(client: TestClient, store: AnnouncementStore) -> None:
    announcement, _baseline = seed_baseline(store)
    _ = store.save_schedule(
        validate_schedule(
            {
                "name": "Tech Net",
                "kind": "weekly",
                "announcement_id": announcement.id,
                "days": ["sun"],
                "slots": ["21:00"],
                "offset_minutes": -5,
            }
        )
    )
    payload = json_dict(
        client.get("/api/schedules", params={"clock_date": "2026-09-06"})
    )
    clock = _dict_list(payload["clock"])
    slot = next(row for row in clock if row["slot"] == "21:00")
    neighbor = next(row for row in clock if row["slot"] == "20:30")
    assert slot["source"] == "overlay"
    assert slot["label"] == "Station ID"
    fire_at = slot["fire_at"]
    assert isinstance(fire_at, str)
    assert "20:55" in fire_at
    assert neighbor["source"] == "baseline"


def test_clock_date_query(client: TestClient, store: AnnouncementStore) -> None:
    _ = seed_baseline(store)
    payload = json_dict(
        client.get("/api/schedules", params={"clock_date": "2026-09-06"})
    )
    clock = _dict_list(payload["clock"])
    assert payload["clock_date"] == "2026-09-06"
    assert clock[0]["slot"] == "00:00"
    slot_at = clock[0]["slot_at"]
    assert isinstance(slot_at, str)
    assert datetime.fromisoformat(slot_at).date() == date(2026, 9, 6)


def test_trigger_now_disabled_hides_endpoint(client: TestClient, store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.TRIGGER_NOW", False)
    _announcement, schedule = seed_baseline(store)
    payload = json_dict(client.get("/api/schedules"))
    assert payload["trigger_now_enabled"] is False
    res = client.post(f"/api/schedules/{schedule.id}/trigger")
    assert res.status_code == 404


def test_trigger_now_plays_without_changing_last_or_next(client: TestClient, store: AnnouncementStore, radio: StubRadio, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.TRIGGER_NOW", True)
    def fake_sleep(_seconds: float) -> None:
        return None

    def fake_chunks(_item: Announcement) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    def fake_play(_chunks: object) -> None:
        return None

    monkeypatch.setattr("app.scheduler_fire.time.sleep", fake_sleep)
    monkeypatch.setattr("app.scheduler_fire.chunks_for_announcement", fake_chunks)
    monkeypatch.setattr("app.scheduler_fire.play_chunks", fake_play)
    _announcement, schedule = seed_baseline(store)
    before = json_dict(client.get("/api/schedules"))
    assert before["trigger_now_enabled"] is True
    row = _dict_list(before["schedules"])[0]
    assert row["last_run_at"] is None
    next_at = row["next_run_at"]
    res = client.post(f"/api/schedules/{schedule.id}/trigger")
    assert res.status_code == 200
    assert json_dict(res)["result"] == "transmitted"
    after = _dict_list(json_dict(client.get("/api/schedules"))["schedules"])[0]
    assert after["last_run_at"] is None
    assert after["next_run_at"] == next_at
    assert ("ptt", True) in radio.events
    assert ("ptt", False) in radio.events


def test_index_shows_branding_before_login(client: TestClient) -> None:
    import html

    from app import __version__
    from app.config import APP_TITLE, STATION_NAME

    page = client.get("/").text
    title = html.escape(APP_TITLE)
    station = html.escape(STATION_NAME)
    assert f"v{__version__}" in page
    assert f"<title>{title}</title>" in page
    heading = page.split('id="app-heading"', 1)[1].split("</h1>", 1)[0]
    assert heading.index(f'id="app-title-heading"') < heading.index('id="station-heading"')
    assert station in heading
    assert title in heading
    assert f'alt="{station}"' in page
    assert "{{APP_VERSION}}" not in page
    assert "{{APP_TITLE}}" not in page
    assert "{{STATION_NAME}}" not in page


def test_installed_version_matches_pyproject() -> None:
    """Fails when the venv is stale after a version bump; run `uv sync`."""
    from app import __version__
    from app.config import ROOT_DIR

    pyproject = _toml(
        (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert __version__ == _dict(pyproject["project"])["version"]


def test_newsline_packages_are_declared_for_deploy() -> None:
    """`uv sync --frozen` on the NUC only installs what pyproject.toml lists."""
    from app.config import ROOT_DIR

    pyproject = _toml(
        (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = _dict(pyproject["project"])["dependencies"]
    if not isinstance(dependencies, list):
        raise AssertionError("dependencies must be a list")
    dependency_values = cast(list[object], dependencies)
    assert all(isinstance(dependency, str) for dependency in dependency_values)
    deps = "\n".join(cast(list[str], dependency_values))
    for name in ("feedparser", "requests", "rapidfuzz", "faster-whisper", "stable-ts", "av", "torch", "torchaudio"):
        assert name in deps, f"{name} must be a project dependency so deploy installs it"
    assert "av>=19" in deps
    lock = (ROOT_DIR / "uv.lock").read_text(encoding="utf-8")
    assert "av-19.0.2-cp312-abi3-manylinux_2_28_x86_64.whl" in lock
    assert "torch-2.14.1%2Bcpu-cp314-cp314-manylinux_2_28_x86_64.whl" in lock
    assert "nvidia-cudnn" not in lock


def test_voices_payload_includes_busy_caps(client: TestClient) -> None:
    from app.config import MAX_BUSY_GIVE_UP_SECONDS, MAX_TEXT_CHARS, PTT_LEAD_SECONDS

    payload = json_dict(client.get("/api/voices"))
    assert payload["max_busy_give_up_seconds"] == MAX_BUSY_GIVE_UP_SECONDS
    assert payload["ptt_lead_seconds"] == PTT_LEAD_SECONDS
    assert payload["max_text_chars"] == MAX_TEXT_CHARS


def test_meta_returns_calendar_labels_and_default_priorities(client: TestClient) -> None:
    from app.models import DAY_LABELS, DAY_NAMES, DEFAULT_PRIORITY, MONTH_LABELS

    payload = json_dict(client.get("/api/meta"))
    assert payload["days"] == [
        {"id": day, "label": DAY_LABELS[day]} for day in DAY_NAMES
    ]
    assert payload["months"] == [
        {"id": month, "label": MONTH_LABELS[month]} for month in range(1, 13)
    ]
    assert payload["default_priority"] == DEFAULT_PRIORITY
