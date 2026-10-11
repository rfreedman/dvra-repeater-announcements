"""Regression tests for fixes recorded in docs/code-review-findings.md."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.engines.piper import PiperEngine
from app.server import app
from app.store import DataFileError
from app.users import UserStore
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def test_schedules_poll_does_not_sample_radio_when_nothing_pending(client, store, radio):
    client.get("/api/schedules")
    assert not any(event[0] == "busy_check" for event in radio.events)


def test_background_poll_does_not_extend_idle_session(users, admin_user, monkeypatch):
    monkeypatch.setattr("app.auth.SESSION_IDLE_SECONDS", 30)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("app.auth.time.time", lambda: clock["t"])
    poll = {"X-Background-Poll": "1"}
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        clock["t"] += 20
        assert client.get("/api/auth/me", headers=poll).status_code == 200
        clock["t"] += 20  # 40 s since the last real activity
        assert client.get("/api/auth/me", headers=poll).status_code == 401


def test_repeated_failed_logins_are_rate_limited(users, admin_user):
    with TestClient(app) as client:
        for _ in range(5):
            res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong-pass"})
            assert res.status_code == 401
        res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        assert res.status_code == 429
        assert "Retry-After" in res.headers


def test_piper_voice_lock_is_not_held_while_consumer_waits(tmp_path):
    engine = PiperEngine(voices_dir=tmp_path)
    voice_id = "en_US-test-medium"

    def sentences(_text, syn_config=None):
        for _ in range(2):
            yield SimpleNamespace(
                audio_int16_bytes=b"\x00\x00",
                sample_rate=22050,
                sample_width=2,
                sample_channels=1,
            )

    engine._models[voice_id] = SimpleNamespace(
        config=SimpleNamespace(sample_rate=22050),
        synthesize=sentences,
    )
    stream = engine.synthesize("One. Two.", voice=voice_id)
    next(stream)
    assert not engine._lock_for(voice_id).locked()
    list(stream)


def test_saving_announcement_does_not_wait_for_pcm_render(client, store, monkeypatch):
    release = threading.Event()
    rendered = threading.Event()

    def slow_warm(_announcement):
        release.wait(5)
        rendered.set()

    monkeypatch.setattr("app.server.warm_announcement", slow_warm)
    started = time.perf_counter()
    res = client.post("/api/announcements", json={"name": "ID", "text": "Hello."})
    assert res.status_code == 200
    assert time.perf_counter() - started < 2
    assert not rendered.is_set()
    release.set()
    assert rendered.wait(5)


def test_dst_transitions_never_produce_duplicate_slot_keys():
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from app.slots import iter_day_slots, iter_slots

    zone = ZoneInfo("America/New_York")
    fall_back = [slot.key for slot in iter_day_slots(date(2026, 11, 1), zone)]
    assert len(fall_back) == len(set(fall_back)) == 48  # repeated 01:xx hour: first pass only
    window = list(
        iter_slots(datetime(2026, 11, 1, 0, 40, tzinfo=zone), datetime(2026, 11, 1, 3, 0, tzinfo=zone), zone)
    )
    assert len(window) == len({slot.key for slot in window})
    spring = [slot.center.strftime("%H:%M") for slot in iter_day_slots(date(2026, 3, 8), zone)]
    assert "02:00" not in spring and "02:30" not in spring and len(spring) == 46


def test_unknown_users_file_is_refused_not_overwritten(tmp_path):
    path = tmp_path / "users.json"
    path.write_text('{"version": 9, "users": []}\n', encoding="utf-8")
    users = UserStore(path)
    with pytest.raises(DataFileError):
        users.count()
    with pytest.raises(DataFileError):
        users.create(username="x", password_hash="h", role="admin")
    assert '"version": 9' in path.read_text(encoding="utf-8")
