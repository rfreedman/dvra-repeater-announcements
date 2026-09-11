from __future__ import annotations

from datetime import date, datetime

from fastapi.testclient import TestClient

from app.audio import PcmChunk
from app.models import Announcement, Schedule
from app.radio import StubRadio, set_radio
from app.server import app


def _seed_baseline(store, name="Station ID"):
    announcement = store.save_announcement(Announcement(name=name, text="This is the ID."))
    schedule = store.save_schedule(
        Schedule(name="Baseline ID", kind="baseline", announcement_id=announcement.id)
    )
    return announcement, schedule


def test_old_nested_json_is_ignored(store):
    store.path.write_text(
        '{"announcements": [{"id": "old", "name": "Legacy", "text": "gone", "schedules": []}]}\n',
        encoding="utf-8",
    )
    assert store.list_announcements() == []
    assert store.list_schedules() == []


def test_empty_store_lists_nothing(store):
    with TestClient(app) as client:
        payload = client.get("/api/schedules").json()
        assert payload["schedules"] == []
        assert payload["upcoming"] is None
        assert payload["clock"]
        assert any("baseline" in item.lower() for item in payload["warnings"])


def test_create_announcement_and_baseline(store):
    with TestClient(app) as client:
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


def test_same_text_can_be_baseline_and_overlay(store):
    announcement, _baseline = _seed_baseline(store)
    with TestClient(app) as client:
        overlay = client.post(
            "/api/schedules",
            json={
                "name": "Weekly Net",
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


def test_duplicate_baseline_announcement_is_conflict(store):
    announcement, _baseline = _seed_baseline(store)
    with TestClient(app) as client:
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


def test_equal_priority_overlays_conflict(store):
    announcement, _baseline = _seed_baseline(store)
    extra = store.save_announcement(Announcement(name="Other", text="Other text."))
    with TestClient(app) as client:
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


def test_cannot_delete_announcement_in_use(store):
    announcement, _baseline = _seed_baseline(store)
    with TestClient(app) as client:
        res = client.delete(f"/api/announcements/{announcement.id}")
        assert res.status_code == 409
        deleted = client.delete(f"/api/schedules/{_baseline.id}")
        assert deleted.status_code == 200
        gone = client.delete(f"/api/announcements/{announcement.id}")
        assert gone.status_code == 200


def test_offset_outside_window_rejected(store):
    announcement, _baseline = _seed_baseline(store)
    with TestClient(app) as client:
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


def test_speak_does_not_key_ptt(store, monkeypatch):
    radio = StubRadio()
    set_radio(radio)

    def fake_synthesize(text, voice=None, speed=1.0, sentence_pause=0.25):
        def chunks():
            yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)

        return "en_US-ryan-medium", 22050, chunks()

    monkeypatch.setattr("app.server.registry.synthesize", fake_synthesize)
    with TestClient(app) as client:
        res = client.post("/api/speak", json={"text": "hello from preview"})
        assert res.status_code == 200
        assert res.content == b"\x00\x00"
    assert not any(event[0] == "ptt" for event in radio.events)
    set_radio(None)


def test_upcoming_is_running_when_a_fire_is_in_progress(store, monkeypatch):
    _seed_baseline(store)
    monkeypatch.setattr(
        "app.server.get_running",
        lambda: {
            "announcement_id": "ann-live",
            "announcement_name": "On-the-hour",
            "schedule_id": "sched-live",
            "started_at": "2026-08-28T12:00:00-04:00",
        },
    )
    with TestClient(app) as client:
        payload = client.get("/api/schedules").json()
        assert payload["upcoming"]["status"] == "running"
        assert payload["upcoming"]["announcement_name"] == "On-the-hour"
        assert payload["upcoming"]["at"] == "2026-08-28T12:00:00-04:00"


def test_weekly_overlay_shows_on_sunday_clock(store):
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
    with TestClient(app) as client:
        payload = client.get("/api/schedules", params={"clock_date": "2026-09-06"}).json()
        slot = next(row for row in payload["clock"] if row["slot"] == "21:00")
        neighbor = next(row for row in payload["clock"] if row["slot"] == "20:30")
        assert slot["source"] == "overlay"
        assert slot["label"] == "Station ID"
        assert "20:55" in slot["fire_at"]
        assert neighbor["source"] == "baseline"


def test_clock_date_query(store):
    _seed_baseline(store)
    with TestClient(app) as client:
        payload = client.get("/api/schedules", params={"clock_date": "2026-09-06"}).json()
        assert payload["clock_date"] == "2026-09-06"
        assert payload["clock"][0]["slot"] == "00:00"
        assert datetime.fromisoformat(payload["clock"][0]["slot_at"]).date() == date(2026, 9, 6)


def test_trigger_now_disabled_hides_endpoint(store, monkeypatch):
    monkeypatch.setattr("app.config.TRIGGER_NOW", False)
    _announcement, schedule = _seed_baseline(store)
    with TestClient(app) as client:
        payload = client.get("/api/schedules").json()
        assert payload["trigger_now_enabled"] is False
        res = client.post(f"/api/schedules/{schedule.id}/trigger")
        assert res.status_code == 404


def test_trigger_now_plays_without_changing_last_or_next(store, radio, monkeypatch):
    monkeypatch.setattr("app.config.TRIGGER_NOW", True)
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "app.scheduler.chunks_for_announcement",
        lambda _item: [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
    )
    monkeypatch.setattr("app.scheduler.play_chunks", lambda _chunks: None)
    _announcement, schedule = _seed_baseline(store)
    with TestClient(app) as client:
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
    assert radio.events[-1] == ("ptt", False)
