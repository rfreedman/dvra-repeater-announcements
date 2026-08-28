from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from app.audio import PcmChunk
from app.models import Announcement, Schedule
from app.radio import StubRadio, set_radio
from app.server import app


def test_flatten_and_delete_one_schedule(store):
    tz = "America/New_York"
    first = Schedule.model_validate(
        {
            "kind": "hourly",
            "minute": 55,
            "timezone": tz,
            "exclusions": [{"kind": "day_time", "days": ["sun"], "start": "21:00", "end": "22:00"}],
        }
    )
    second = Schedule.model_validate({"kind": "daily", "times": ["19:00"], "timezone": tz})
    store.save(
        Announcement(name="Station ID", text="ID", schedules=[first, second]),
    )
    with TestClient(app) as client:
        listed = client.get("/api/schedules").json()["schedules"]
        assert len(listed) == 2
        hourly = next(row for row in listed if "Every hour at :55" in row["summary"])
        assert "except Sunday 21:00–22:00" in hourly["summary"]
        assert hourly["next_run_at"]
        nxt = datetime.fromisoformat(hourly["next_run_at"])
        assert nxt.tzinfo is not None
        assert not (nxt.weekday() == 6 and nxt.hour == 21 and nxt.minute == 55)

        other_id = next(row["schedule_id"] for row in listed if row["schedule_id"] != hourly["schedule_id"])
        deleted = client.delete(f"/api/announcements/{hourly['announcement_id']}/schedules/{hourly['schedule_id']}")
        assert deleted.status_code == 200
        remaining = client.get("/api/schedules").json()["schedules"]
        assert len(remaining) == 1
        assert remaining[0]["schedule_id"] == other_id
        assert remaining[0]["summary"].startswith("19:00 every day")
        payload = client.get("/api/schedules").json()
        assert payload["upcoming"]["status"] == "waiting"
        assert payload["upcoming"]["announcement_name"]
        assert payload["upcoming"]["at"]


def test_unscheduled_announcement_is_listed_and_deleted(store):
    stored = store.save(Announcement(name="Tech Net Pre-Announcement", text="Stand by", schedules=[]))
    with TestClient(app) as client:
        listed = client.get("/api/schedules").json()["schedules"]
        assert len(listed) == 1
        row = listed[0]
        assert row["announcement_id"] == stored.id
        assert row["schedule_id"] is None
        assert row["summary"] == "No schedule"
        assert row["warning"] == "Won't play until a schedule is saved."
        assert row["next_run_at"] is None
        deleted = client.delete(f"/api/announcements/{stored.id}")
        assert deleted.status_code == 200
        assert client.get("/api/schedules").json()["schedules"] == []
        assert client.get("/api/schedules").json()["upcoming"] is None


def test_create_rejects_conflicting_schedule(store):
    store.save(
        Announcement(
            name="Station ID",
            text="ID",
            schedules=[
                Schedule.model_validate(
                    {"kind": "hourly", "minutes": [55], "timezone": "America/New_York"}
                )
            ],
        )
    )
    with TestClient(app) as client:
        res = client.post(
            "/api/announcements",
            json={
                "name": "Also ID",
                "text": "Hello",
                "schedule": {"kind": "hourly", "minutes": [55]},
            },
        )
        assert res.status_code == 409
        assert "Conflicts with" in res.json()["detail"]


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
    store.save(
        Announcement(
            name="Waiting ID",
            text="ID",
            schedules=[
                Schedule.model_validate(
                    {"kind": "hourly", "minutes": [0], "timezone": "America/New_York"}
                )
            ],
        )
    )
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
