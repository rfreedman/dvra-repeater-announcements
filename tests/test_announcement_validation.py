from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import MAX_TEXT_CHARS
from app.models import Announcement, StoreDocument


def test_announcement_rejects_speed_out_of_bounds():
    with pytest.raises(ValidationError):
        Announcement(name="Test", text="Hello.", speed=0.1)
    with pytest.raises(ValidationError):
        Announcement(name="Test", text="Hello.", speed=3.0)


def test_announcement_rejects_busy_retry_out_of_bounds():
    with pytest.raises(ValidationError):
        Announcement(name="Test", text="Hello.", busy_retry_seconds=200)


def test_announcement_rejects_empty_text_after_strip():
    with pytest.raises(ValidationError):
        Announcement(name="Test", text="   ")


def test_announcement_rejects_text_over_max_length():
    with pytest.raises(ValidationError):
        Announcement(name="Test", text="x" * (MAX_TEXT_CHARS + 1))


def test_store_document_rejects_invalid_announcement_on_load(store):
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

    store.path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        store.list_announcements()


def test_api_rejects_invalid_announcement_speed(client):
    res = client.post(
        "/api/announcements",
        json={"name": "Test", "text": "Hello.", "speed": 0.1},
    )
    assert res.status_code == 422


def test_api_rejects_whitespace_only_text(client):
    res = client.post(
        "/api/announcements",
        json={"name": "Test", "text": "   "},
    )
    assert res.status_code == 422


def test_api_update_rejects_invalid_busy_give_up(client, store):
    ann = store.save_announcement(Announcement(name="Test", text="Hello."))
    res = client.put(
        f"/api/announcements/{ann.id}",
        json={
            "name": "Test",
            "text": "Hello.",
            "busy_give_up_seconds": 900,
        },
    )
    assert res.status_code == 422


def test_valid_announcement_round_trips_through_store(store):
    ann = Announcement(
        name="Test",
        text="  On the air.  ",
        speed=1.5,
        sentence_pause=0.5,
    )
    stored = store.save_announcement(ann)
    assert stored.text == "On the air."
    loaded = store.get(stored.id)
    assert loaded is not None
    assert loaded.speed == 1.5
    doc = StoreDocument.model_validate(
        {"version": 2, "announcements": [stored.model_dump(mode="json")], "schedules": []}
    )
    assert doc.announcements[0].text == "On the air."
