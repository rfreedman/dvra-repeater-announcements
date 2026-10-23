from __future__ import annotations

from fastapi.testclient import TestClient

from app.audio import PcmChunk
from app.server import app
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def test_auth_status_needs_setup(users):
    with TestClient(app) as client:
        payload = client.get("/api/auth/status").json()
        assert payload["needs_setup"] is True
        assert payload["authenticated"] is False


def test_setup_creates_admin_and_logs_in(users):
    with TestClient(app) as client:
        res = client.post(
            "/api/auth/setup",
            json={"username": "boss", "password": "password123"},
        )
        assert res.status_code == 200
        assert res.json()["user"]["role"] == "admin"
        status = client.get("/api/auth/status").json()
        assert status["authenticated"] is True
        assert status["user"]["username"] == "boss"
        again = client.post(
            "/api/auth/setup",
            json={"username": "other", "password": "password123"},
        )
        assert again.status_code == 409


def test_setup_rejects_short_password(users):
    with TestClient(app) as client:
        res = client.post(
            "/api/auth/setup",
            json={"username": "boss", "password": "short"},
        )
        assert res.status_code == 400


def test_login_logout(users, admin_user):
    with TestClient(app) as client:
        bad = client.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": "wrong-password"},
        )
        assert bad.status_code == 401
        ok = client.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert ok.status_code == 200
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_unauthenticated_api_is_rejected(users, admin_user):
    with TestClient(app) as client:
        assert client.get("/api/schedules").status_code == 401
        assert client.get("/api/announcements").status_code == 401
        assert client.get("/api/health").status_code == 200


def test_readonly_can_view_and_speak_but_not_mutate(readonly_client, store, monkeypatch):
    from app.models import Announcement

    item = store.save_announcement(Announcement(name="ID", text="Hello."))
    assert readonly_client.get("/api/announcements").status_code == 200
    assert readonly_client.get("/api/schedules").status_code == 200
    assert readonly_client.get("/api/users").status_code == 403
    assert (
        readonly_client.post(
            "/api/announcements",
            json={"name": "Nope", "text": "Blocked."},
        ).status_code
        == 403
    )
    assert (
        readonly_client.put(
            f"/api/announcements/{item.id}",
            json={"name": "Nope", "text": "Blocked."},
        ).status_code
        == 403
    )
    assert readonly_client.delete(f"/api/announcements/{item.id}").status_code == 403
    assert (
        readonly_client.put(
            "/api/settings",
            json={"baseline_randomize": True},
        ).status_code
        == 403
    )

    def fake_synthesize(text, voice=None, speed=1.0, sentence_pause=0.25):
        def chunks():
            yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)

        return "en_US-ryan-medium", 22050, chunks()

    monkeypatch.setattr("app.routes.speak.registry.stream_synthesize", fake_synthesize)
    speak = readonly_client.post("/api/speak", json={"text": "preview only"})
    assert speak.status_code == 200
    assert speak.content == b"\x00\x00"


def test_readonly_cannot_trigger(readonly_client, store, monkeypatch):
    from app.models import Announcement, Schedule

    monkeypatch.setattr("app.config.TRIGGER_NOW", True)
    announcement = store.save_announcement(Announcement(name="ID", text="Hello."))
    schedule = store.save_schedule(
        Schedule(name="Baseline", kind="baseline", announcement_id=announcement.id)
    )
    res = readonly_client.post(f"/api/schedules/{schedule.id}/trigger")
    assert res.status_code == 403


def test_admin_user_management(client, users, admin_user):
    created = client.post(
        "/api/users",
        json={"username": "ops", "password": "password123", "role": "readonly"},
    )
    assert created.status_code == 200
    user_id = created.json()["id"]
    listed = client.get("/api/users").json()["users"]
    assert {row["username"] for row in listed} == {ADMIN_USERNAME, "ops"}

    # Cannot remove the only admin.
    assert client.put(f"/api/users/{admin_user.id}", json={"role": "readonly"}).status_code == 400
    assert client.delete(f"/api/users/{admin_user.id}").status_code == 400

    promoted = client.put(f"/api/users/{user_id}", json={"role": "admin"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    # With two admins, demoting/deleting one is allowed.
    assert client.put(f"/api/users/{user_id}", json={"role": "readonly"}).status_code == 200
    assert client.delete(f"/api/users/{user_id}").status_code == 200
    assert {row["username"] for row in client.get("/api/users").json()["users"]} == {ADMIN_USERNAME}


def test_duplicate_username_rejected(client, users, admin_user):
    res = client.post(
        "/api/users",
        json={"username": ADMIN_USERNAME, "password": "password123", "role": "readonly"},
    )
    assert res.status_code == 400


def test_login_limiter_uses_startup_config():
    from app.auth import login_limiter
    from app.config import LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS

    assert login_limiter.max_failures == LOGIN_MAX_FAILURES
    assert login_limiter.window_seconds == LOGIN_WINDOW_SECONDS


def test_login_limit_env_invalid_falls_back(monkeypatch, caplog):
    import logging

    from app.config import login_max_failures, login_window_seconds

    monkeypatch.delenv("TTS_LOGIN_MAX_FAILURES", raising=False)
    monkeypatch.delenv("TTS_LOGIN_WINDOW_SECONDS", raising=False)
    assert login_max_failures() == 5
    assert login_window_seconds() == 300.0

    monkeypatch.setenv("TTS_LOGIN_MAX_FAILURES", "0")
    monkeypatch.setenv("TTS_LOGIN_WINDOW_SECONDS", "nope")
    with caplog.at_level(logging.WARNING, logger="app.config"):
        assert login_max_failures() == 5
        assert login_window_seconds() == 300.0
    assert "TTS_LOGIN_MAX_FAILURES" in caplog.text
    assert "TTS_LOGIN_WINDOW_SECONDS" in caplog.text


def test_login_rate_limit_follows_env(users, admin_user, monkeypatch):
    """A limiter built the same way as startup honors TTS_LOGIN_* (issue #14)."""
    from app.auth import configured_login_limiter

    monkeypatch.setenv("TTS_LOGIN_MAX_FAILURES", "2")
    monkeypatch.setenv("TTS_LOGIN_WINDOW_SECONDS", "90")
    monkeypatch.setattr("app.routes.auth.login_limiter", configured_login_limiter())
    with TestClient(app) as client:
        for _ in range(2):
            res = client.post(
                "/api/auth/login",
                json={"username": ADMIN_USERNAME, "password": "wrong-pass"},
            )
            assert res.status_code == 401
        res = client.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert res.status_code == 429
        assert int(res.headers["Retry-After"]) <= 91


def test_idle_timeout_clears_session(users, admin_user, monkeypatch):
    monkeypatch.setattr("app.auth.SESSION_IDLE_SECONDS", 30)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("app.auth.time.time", lambda: clock["t"])
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/auth/login",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            ).status_code
            == 200
        )
        assert client.get("/api/auth/me").status_code == 200
        clock["t"] += 31
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/auth/status").json()["authenticated"] is False
