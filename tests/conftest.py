from __future__ import annotations

import os

# Must be set before app.server imports SessionMiddleware.
os.environ.setdefault("TTS_SESSION_SECRET", "test-session-secret-for-pytest")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.radio import StubRadio, set_radio
from app.server import app
from app.store import AnnouncementStore, set_store
from app.users import UserStore, set_user_store

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "test-password"


@pytest.fixture(autouse=True)
def no_voice_preload(monkeypatch):
    monkeypatch.setattr("app.registry.EngineRegistry.preload", lambda self: [])


@pytest.fixture(autouse=True)
def no_pcm_warm(monkeypatch, tmp_path):
    from app.pcm_cache import PcmCache, set_pcm_cache

    set_pcm_cache(PcmCache(tmp_path / "pcm-cache"))
    monkeypatch.setattr("app.server.warm_announcement", lambda announcement: None)
    monkeypatch.setattr("app.server.warm_all", lambda announcements=None: None)
    yield
    set_pcm_cache(None)


@pytest.fixture
def store(tmp_path: Path):
    path = tmp_path / "announcements.json"
    item = AnnouncementStore(path)
    set_store(item)
    yield item
    set_store(None)


@pytest.fixture
def users(tmp_path: Path):
    path = tmp_path / "users.json"
    item = UserStore(path)
    set_user_store(item)
    yield item
    set_user_store(None)


@pytest.fixture
def admin_user(users: UserStore):
    return users.create(
        username=ADMIN_USERNAME,
        password_hash=hash_password(ADMIN_PASSWORD),
        role="admin",
    )


@pytest.fixture
def readonly_user(users: UserStore):
    return users.create(
        username="viewer",
        password_hash=hash_password(ADMIN_PASSWORD),
        role="readonly",
    )


@pytest.fixture
def client(store, admin_user):
    with TestClient(app) as test_client:
        res = test_client.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert res.status_code == 200
        yield test_client


@pytest.fixture
def readonly_client(store, admin_user, readonly_user):
    with TestClient(app) as test_client:
        res = test_client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": ADMIN_PASSWORD},
        )
        assert res.status_code == 200
        yield test_client


@pytest.fixture
def radio():
    item = StubRadio()
    set_radio(item)
    yield item
    set_radio(None)
