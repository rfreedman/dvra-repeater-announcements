from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import MAX_TEXT_CHARS
from fastapi.testclient import TestClient

from app.models import StoreDocument, TtsAnnouncement
from app.store import AnnouncementStore


def test_announcement_rejects_speed_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        _ = TtsAnnouncement(name="Test", text="Hello.", speed=0.1)
    with pytest.raises(ValidationError):
        _ = TtsAnnouncement(name="Test", text="Hello.", speed=3.0)


def test_announcement_rejects_busy_retry_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        _ = TtsAnnouncement(name="Test", text="Hello.", busy_retry_seconds=200)


def test_announcement_rejects_empty_text_after_strip() -> None:
    with pytest.raises(ValidationError):
        _ = TtsAnnouncement(name="Test", text="   ")


def test_announcement_rejects_text_over_max_length() -> None:
    from app.schemas import TtsAnnouncementIn

    with pytest.raises(ValidationError):
        _ = TtsAnnouncementIn(name="Test", text="x" * (MAX_TEXT_CHARS + 1))


def test_store_document_rejects_invalid_announcement_on_load(store: AnnouncementStore) -> None:
    payload = {
        "version": 2,
        "announcements": [
            {
                "id": "bad1",
                "name": "Bad",
                "text": "Hi",
                "speed": 0.1,
            }
        ],
        "schedules": [],
    }
    import json

    _ = store.path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        _ = store.list_announcements()


def test_api_rejects_invalid_announcement_speed(client: TestClient) -> None:
    res = client.post(
        "/api/announcements",
        json={"name": "Test", "text": "Hello.", "speed": 0.1},
    )
    assert res.status_code == 422


def test_api_rejects_whitespace_only_text(client: TestClient) -> None:
    res = client.post(
        "/api/announcements",
        json={"name": "Test", "text": "   "},
    )
    assert res.status_code == 422


def test_api_update_rejects_invalid_busy_give_up(client: TestClient, store: AnnouncementStore) -> None:
    ann = store.save_announcement(TtsAnnouncement(name="Test", text="Hello."))
    res = client.put(
        f"/api/announcements/{ann.id}",
        json={
            "name": "Test",
            "text": "Hello.",
            "busy_give_up_seconds": 900,
        },
    )
    assert res.status_code == 422


def test_valid_announcement_round_trips_through_store(store: AnnouncementStore) -> None:
    ann = TtsAnnouncement(
        name="Test",
        text="  On the air.  ",
        speed=1.5,
        sentence_pause=0.5,
    )
    stored = store.save_announcement(ann)
    assert isinstance(stored, TtsAnnouncement)
    assert stored.text == "On the air."
    loaded = store.get(stored.id)
    assert loaded is not None
    assert isinstance(loaded, TtsAnnouncement)
    assert loaded.kind == "tts"
    assert loaded.speed == 1.5
    doc = StoreDocument.model_validate(
        {"version": 2, "announcements": [stored.model_dump(mode="json")], "schedules": []}
    )
    first = doc.announcements[0]
    assert isinstance(first, TtsAnnouncement)
    assert first.kind == "tts"
    assert first.text == "On the air."


def test_old_flat_newsline_json_loads_and_saves_lean() -> None:
    from app.models import validate_announcement

    row = validate_announcement(
        {
            "name": "Newsline",
            "kind": "newsline",
            "text": "",
            "voice": None,
            "speed": 1.0,
            "sentence_pause": 0.25,
            "play_seconds": None,
            "too_long": False,
            "station_id_announcement_id": "station",
            "opening_announcement_id": None,
            "closing_announcement_id": None,
            "resume_announcement_id": None,
            "ptt_gap_seconds": 2.0,
        }
    )
    dumped = row.model_dump()
    assert dumped["kind"] == "newsline"
    assert "text" not in dumped
    assert "play_seconds" not in dumped
    assert dumped["station_id_announcement_id"] == "station"
