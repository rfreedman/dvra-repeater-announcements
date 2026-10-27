from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from app.audio import PcmChunk
from app.models import Announcement, Schedule
from app.radio import StubRadio, set_radio
from app.store import DataFileError


def _seed_baseline(store, name="Station ID"):
    announcement = store.save_announcement(Announcement(name=name, text="This is the ID."))
    schedule = store.save_schedule(
        Schedule(name="Baseline ID", kind="baseline", announcement_id=announcement.id)
    )
    return announcement, schedule


def test_unversioned_json_is_refused_not_overwritten(store):
    legacy = '{"announcements": [{"id": "old", "name": "Legacy", "text": "gone", "schedules": []}]}\n'
    store.path.write_text(legacy, encoding="utf-8")
    with pytest.raises(DataFileError):
        store.list_announcements()
    with pytest.raises(DataFileError):
        store.save_announcement(Announcement(name="New", text="Hello"))
    assert store.path.read_text(encoding="utf-8") == legacy


def test_empty_json_file_is_an_empty_store(store):
    store.path.write_text("{}\n", encoding="utf-8")
    assert store.list_announcements() == []


def test_empty_store_lists_nothing(client, store):
    payload = client.get("/api/schedules").json()
    assert payload["schedules"] == []
    assert payload["upcoming"] is None
    assert payload["clock"]
    assert any("baseline" in item.lower() for item in payload["warnings"])


def test_once_and_single_day_schedules_are_dropped_on_read(store):
    announcement, baseline = _seed_baseline(store)
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["schedules"].extend(
        [
            {
                "id": "deadonce",
                "name": "Old once",
                "enabled": True,
                "kind": "once",
                "announcement_id": announcement.id,
                "slots": ["12:00"],
                "days": [],
                "priority": 50,
            },
            {
                "id": "deadsingle",
                "name": "Old single day",
                "enabled": True,
                "kind": "single_day",
                "announcement_id": announcement.id,
                "slots": ["12:00"],
                "days": [],
                "priority": 50,
            },
        ]
    )
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    ids = {item.id for item in store.list_schedules()}
    assert baseline.id in ids
    assert "deadonce" not in ids
    assert "deadsingle" not in ids
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert all(row.get("kind") not in {"once", "single_day"} for row in saved["schedules"])


def test_create_announcement_and_baseline(client, store):
    created = client.post(
        "/api/announcements",
        json={"name": "Station ID", "text": "This is w2-zee-q."},
    )
    assert created.status_code == 200
    announcement_id = created.json()["id"]
    assert "schedules" not in created.json()
    scheduled = client.post(
        "/api/schedules",
        json={
            "name": "Baseline ID",
            "kind": "baseline",
            "announcement_id": announcement_id,
        },
    )
    assert scheduled.status_code == 200
    listed = client.get("/api/schedules").json()
    assert listed["schedules"][0]["kind"] == "baseline"
    assert listed["upcoming"]["announcement_name"] == "Station ID"
    assert listed["clock"][0]["source"] == "baseline"


def test_same_text_can_be_baseline_and_overlay(client, store):
    announcement, _baseline = _seed_baseline(store)
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
    listed = client.get("/api/schedules").json()["schedules"]
    assert len(listed) == 2
    assert {row["kind"] for row in listed} == {"baseline", "weekly"}


def test_home_when_column_collapses_contiguous_slots_and_days(client, store):
    announcement, _baseline = _seed_baseline(store)
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
    rows = client.get("/api/schedules").json()["schedules"]
    drive = next(row for row in rows if row["name"] == "Drive time")
    assert drive["summary"] == "the 07:00–08:00 slots and the 17:00 slot on Monday–Friday"


def test_home_when_column_says_every_day(client, store):
    announcement, _baseline = _seed_baseline(store)
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
    rows = client.get("/api/schedules").json()["schedules"]
    daily = next(row for row in rows if row["name"] == "Daily ID extra")
    assert daily["summary"] == "the 12:00 slot every day"


def test_duplicate_baseline_announcement_is_conflict(client, store):
    announcement, _baseline = _seed_baseline(store)
    res = client.post(
        "/api/schedules",
        json={
            "name": "Also baseline",
            "kind": "baseline",
            "announcement_id": announcement.id,
        },
    )
    assert res.status_code == 409
    assert "already in the baseline pool" in res.json()["detail"]


def test_equal_priority_overlays_conflict(client, store):
    announcement, _baseline = _seed_baseline(store)
    extra = store.save_announcement(Announcement(name="Other", text="Other text."))
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
    assert "Conflicts with Net A" in second.json()["detail"]


def test_cannot_delete_announcement_in_use(client, store):
    announcement, _baseline = _seed_baseline(store)
    res = client.delete(f"/api/announcements/{announcement.id}")
    assert res.status_code == 409
    deleted = client.delete(f"/api/schedules/{_baseline.id}")
    assert deleted.status_code == 200
    gone = client.delete(f"/api/announcements/{announcement.id}")
    assert gone.status_code == 200


def test_offset_outside_window_rejected(client, store):
    announcement, _baseline = _seed_baseline(store)
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
    assert "±10" in res.json()["detail"]


def test_speak_does_not_key_ptt(client, store, monkeypatch):
    radio = StubRadio()
    set_radio(radio)

    def fake_synthesize(text, voice=None, speed=1.0, sentence_pause=0.25):
        def chunks():
            yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)

        return "en_US-ryan-medium", 22050, chunks()

    monkeypatch.setattr("app.routes.speak.registry.stream_synthesize", fake_synthesize)
    res = client.post("/api/speak", json={"text": "hello from preview"})
    assert res.status_code == 200
    assert res.content == b"\x00\x00"
    assert not any(event[0] == "ptt" for event in radio.events)
    set_radio(None)


def test_upcoming_is_running_when_a_fire_is_in_progress(client, store, monkeypatch):
    _seed_baseline(store)
    monkeypatch.setattr(
        "app.home_live.get_running",
        lambda: {
            "announcement_id": "ann-live",
            "announcement_name": "On-the-hour",
            "schedule_id": "sched-live",
            "started_at": "2026-08-28T12:00:00-04:00",
        },
    )
    payload = client.get("/api/schedules").json()
    assert payload["upcoming"]["status"] == "running"
    assert payload["upcoming"]["announcement_name"] == "On-the-hour"
    assert payload["upcoming"]["at"] == "2026-08-28T12:00:00-04:00"


def test_weekly_overlay_shows_on_sunday_clock(client, store):
    announcement, _baseline = _seed_baseline(store)
    store.save_schedule(
        Schedule.model_validate(
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
    payload = client.get("/api/schedules", params={"clock_date": "2026-09-06"}).json()
    slot = next(row for row in payload["clock"] if row["slot"] == "21:00")
    neighbor = next(row for row in payload["clock"] if row["slot"] == "20:30")
    assert slot["source"] == "overlay"
    assert slot["label"] == "Station ID"
    assert "20:55" in slot["fire_at"]
    assert neighbor["source"] == "baseline"


def test_clock_date_query(client, store):
    _seed_baseline(store)
    payload = client.get("/api/schedules", params={"clock_date": "2026-09-06"}).json()
    assert payload["clock_date"] == "2026-09-06"
    assert payload["clock"][0]["slot"] == "00:00"
    assert datetime.fromisoformat(payload["clock"][0]["slot_at"]).date() == date(2026, 9, 6)


def test_trigger_now_disabled_hides_endpoint(client, store, monkeypatch):
    monkeypatch.setattr("app.config.TRIGGER_NOW", False)
    _announcement, schedule = _seed_baseline(store)
    payload = client.get("/api/schedules").json()
    assert payload["trigger_now_enabled"] is False
    res = client.post(f"/api/schedules/{schedule.id}/trigger")
    assert res.status_code == 404


def test_trigger_now_plays_without_changing_last_or_next(client, store, radio, monkeypatch):
    monkeypatch.setattr("app.config.TRIGGER_NOW", True)
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "app.scheduler.chunks_for_announcement",
        lambda _item: [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
    )
    monkeypatch.setattr("app.scheduler.play_chunks", lambda _chunks: None)
    _announcement, schedule = _seed_baseline(store)
    before = client.get("/api/schedules").json()
    assert before["trigger_now_enabled"] is True
    row = before["schedules"][0]
    assert row["last_run_at"] is None
    next_at = row["next_run_at"]
    res = client.post(f"/api/schedules/{schedule.id}/trigger")
    assert res.status_code == 200
    assert res.json()["result"] == "transmitted"
    after = client.get("/api/schedules").json()["schedules"][0]
    assert after["last_run_at"] is None
    assert after["next_run_at"] == next_at
    assert ("ptt", True) in radio.events
    assert ("ptt", False) in radio.events


def test_index_shows_app_version(client):
    from app import __version__

    html = client.get("/").text
    assert f"v{__version__}" in html
    assert "{{APP_VERSION}}" not in html


def test_installed_version_matches_pyproject():
    """Fails when the venv is stale after a version bump; run `uv sync`."""
    tomllib = pytest.importorskip("tomllib")
    from app import __version__
    from app.config import ROOT_DIR

    pyproject = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == pyproject["project"]["version"]


def test_newsline_packages_are_declared_for_deploy():
    """`uv sync --frozen` on the NUC only installs what pyproject.toml lists."""
    tomllib = pytest.importorskip("tomllib")
    from app.config import ROOT_DIR

    deps = "\n".join(
        tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    )
    for name in ("feedparser", "requests", "rapidfuzz", "faster-whisper", "stable-ts", "av", "torch", "torchaudio"):
        assert name in deps, f"{name} must be a project dependency so deploy installs it"
    assert "av>=19" in deps
    lock = (ROOT_DIR / "uv.lock").read_text(encoding="utf-8")
    assert "av-19.0.2-cp312-abi3-manylinux_2_28_x86_64.whl" in lock
    assert "torch-2.14.1%2Bcpu-cp314-cp314-manylinux_2_28_x86_64.whl" in lock
    assert "nvidia-cudnn" not in lock


def test_voices_payload_includes_busy_caps(client):
    from app.config import MAX_BUSY_GIVE_UP_SECONDS, PTT_LEAD_SECONDS

    payload = client.get("/api/voices").json()
    assert payload["max_busy_give_up_seconds"] == MAX_BUSY_GIVE_UP_SECONDS
    assert payload["ptt_lead_seconds"] == PTT_LEAD_SECONDS


def test_meta_returns_calendar_labels_and_default_priorities(client):
    from app.models import DAY_LABELS, DAY_NAMES, DEFAULT_PRIORITY, MONTH_LABELS

    payload = client.get("/api/meta").json()
    assert payload["days"] == [{"id": day, "label": DAY_LABELS[day]} for day in DAY_NAMES]
    assert payload["months"] == [
        {"id": month, "label": MONTH_LABELS[month]} for month in range(1, 13)
    ]
    assert payload["default_priority"] == DEFAULT_PRIORITY
