"""Regression tests for the October 2026 review fixes."""
from __future__ import annotations
from app.radio import StubRadio

import threading
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from fastapi.testclient import TestClient

from app.auth import BACKGROUND_POLL_HEADER
from app.models import Announcement
from app.tts.piper import PiperEngine
from app.server import app
from app.store import DataFileError
from app.users import User, UserStore
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

from pathlib import Path
from app.store import AnnouncementStore
if TYPE_CHECKING:
    from piper import PiperVoice


def test_schedules_poll_does_not_sample_radio_when_nothing_pending(client: TestClient, store: AnnouncementStore, radio: StubRadio) -> None:
    _ = store
    _ = client.get("/api/schedules")
    assert not any(event[0] == "busy_check" for event in radio.events)


@pytest.mark.parametrize("value", ["1", "0", "true"])
def test_background_poll_does_not_extend_idle_session(users: UserStore, admin_user: User, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Header presence alone marks a background poll; its value is ignored."""
    _ = users
    _ = admin_user
    monkeypatch.setattr("app.config.SESSION_IDLE_SECONDS", 30)
    clock = {"t": 1_000_000.0}
    def fake_time() -> float:
        return clock["t"]

    monkeypatch.setattr("app.auth.time.time", fake_time)
    poll: dict[str, str] = {BACKGROUND_POLL_HEADER: value}
    with TestClient(app) as client:
        _ = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        clock["t"] += 20
        assert client.get("/api/auth/me", headers=poll).status_code == 200
        clock["t"] += 20  # 40 s since the last real activity
        assert client.get("/api/auth/me", headers=poll).status_code == 401


def test_request_without_background_header_extends_idle_session(users: UserStore, admin_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = users
    _ = admin_user
    monkeypatch.setattr("app.config.SESSION_IDLE_SECONDS", 30)
    clock = {"t": 1_000_000.0}
    def fake_time() -> float:
        return clock["t"]

    monkeypatch.setattr("app.auth.time.time", fake_time)
    with TestClient(app) as client:
        _ = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        clock["t"] += 20
        assert client.get("/api/auth/me").status_code == 200
        clock["t"] += 20  # 40 s since login, 20 s since the last request
        assert client.get("/api/auth/me").status_code == 200


def test_repeated_failed_logins_are_rate_limited(users: UserStore, admin_user: User) -> None:
    _ = users
    _ = admin_user
    with TestClient(app) as client:
        for _ in range(5):
            res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong-pass"})
            assert res.status_code == 401
        res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        assert res.status_code == 429
        assert "Retry-After" in res.headers


def test_piper_voice_lock_is_not_held_while_consumer_waits(tmp_path: Path) -> None:
    engine = PiperEngine(voices_dir=tmp_path)
    voice_id = "en_US-test-medium"

    def sentences(_text: str, syn_config: object | None = None) -> Iterator[SimpleNamespace]:
        _ = syn_config
        for _ in range(2):
            yield SimpleNamespace(
                audio_int16_bytes=b"\x00\x00",
                sample_rate=22050,
                sample_width=2,
                sample_channels=1,
            )

    engine.store_voice(
        voice_id,
        cast(
            "PiperVoice",
            cast(
                object,
                SimpleNamespace(
                    config=SimpleNamespace(sample_rate=22050),
                    synthesize=sentences,
                ),
            ),
        ),
    )
    stream = engine.synthesize("One. Two.", voice=voice_id)
    _ = next(stream)
    assert not engine.voice_lock(voice_id).locked()
    _ = list(stream)


def test_saving_announcement_does_not_wait_for_pcm_render(client: TestClient, store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = store
    release = threading.Event()
    rendered = threading.Event()

    def slow_warm(_announcement: Announcement) -> None:
        _ = release.wait(5)
        rendered.set()

    monkeypatch.setattr("app.routes.announcements.warm_announcement", slow_warm)
    started = time.perf_counter()
    res = client.post("/api/announcements", json={"name": "ID", "text": "Hello."})
    assert res.status_code == 200
    assert time.perf_counter() - started < 2
    assert not rendered.is_set()
    release.set()
    assert rendered.wait(5)


def test_dst_transitions_never_produce_duplicate_slot_keys() -> None:
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


def test_unknown_users_file_is_refused_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "users.json"
    _ = path.write_text('{"version": 9, "users": []}\n', encoding="utf-8")
    users = UserStore(path)
    with pytest.raises(DataFileError):
        _ = users.count()
    with pytest.raises(DataFileError):
        _ = users.create(username="x", password_hash="h", role="admin")
    assert '"version": 9' in path.read_text(encoding="utf-8")
