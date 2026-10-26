from __future__ import annotations

import os

# Must be set before any app module is imported (app.config reads them at import).
os.environ.setdefault("TTS_SESSION_SECRET", "test-session-secret-for-pytest")
os.environ["TTS_SKIP_DOTENV"] = "1"

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

_CM119_PORTAUDIO_NAME = "USB PnP Sound Device: Audio (hw:1,0)"


def _fake_portaudio_device_dict(index: int) -> dict[str, object]:
    return {
        "name": _CM119_PORTAUDIO_NAME,
        "index": index,
        "hostapi": 0,
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_low_input_latency": 0.01,
        "default_low_output_latency": 0.01,
        "default_high_input_latency": 0.05,
        "default_high_output_latency": 0.05,
        "default_samplerate": 44100.0,
    }


def _fake_sounddevice_query_devices(device=None, kind=None):
    import sounddevice as sd

    if kind not in ("input", "output", None):
        raise ValueError(f"Invalid kind: {kind!r}")
    if device is None and kind is None:
        return sd.DeviceList(_fake_portaudio_device_dict(i) for i in range(2))
    if device is None:
        return _fake_portaudio_device_dict(0)
    if isinstance(device, str):
        return _fake_portaudio_device_dict(0)
    return _fake_portaudio_device_dict(int(device))


@pytest.fixture(autouse=True)
def headless_portaudio_on_ci(monkeypatch):
    """Headless GitHub runners expose PortAudio without usable device indices."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    monkeypatch.setattr("sounddevice.query_devices", _fake_sounddevice_query_devices)


@pytest.fixture
def stub_dra_playback_dependencies(monkeypatch):
    """Standard mocks for ``play_pcm_chunks_to_dra_devices`` on Linux/CI."""
    monkeypatch.setattr(
        "app.dra_alsa_mixer.sync_cm119_speaker_unmute_for_indices",
        lambda _indices: None,
    )
    monkeypatch.setattr(
        "app.dra_pulse_mixer.sync_pulse_sink_unmute_for_portaudio_index",
        lambda _index: None,
    )
    monkeypatch.setattr("app.dra_playback.dra_device_samplerate", lambda _i: 44100)
    monkeypatch.setattr("app.dra_playback._linux_use_aplay", lambda: False)


@pytest.fixture(autouse=True)
def fire_wall_clock_for_tests(monkeypatch):
    """Keep give-up / defer logic aligned with frozen ``deps.now`` in unit tests."""
    import app.fire as fire_mod

    monkeypatch.setattr(fire_mod, "_wall_now", lambda deps: deps.now)


@pytest.fixture(autouse=True)
def no_voice_preload(monkeypatch):
    monkeypatch.setattr("app.registry.EngineRegistry.preload", lambda self: [])


@pytest.fixture(autouse=True)
def keep_test_radio(monkeypatch):
    """Server startup must not replace the radio a test registered (``radio`` fixture)."""
    monkeypatch.setattr("app.server.init_radio_at_startup", lambda: None)
    yield
    set_radio(None)


@pytest.fixture(autouse=True)
def reset_login_limiter():
    from app.auth import login_limiter

    login_limiter.reset()
    yield
    login_limiter.reset()


@pytest.fixture(autouse=True)
def no_newsline_poll(monkeypatch):
    """Scheduler startup and settings saves must not fetch the Newsline feed."""
    monkeypatch.setattr("app.newsline.poll.install_news_poll", lambda: None)
    monkeypatch.setattr("app.newsline.poll.arm_next_poll", lambda **_kwargs: None)


def _short_measure(announcement, timeout):
    from app.spoken_length import apply_played_length

    if announcement.kind != "tts":
        return announcement
    return apply_played_length(announcement, 1.0, timeout)


@pytest.fixture(autouse=True)
def no_pcm_warm(monkeypatch, tmp_path):
    from app.pcm_cache import PcmCache, set_pcm_cache

    set_pcm_cache(PcmCache(tmp_path / "pcm-cache"))
    monkeypatch.setattr("app.routes.announcements.warm_announcement", lambda announcement: None)
    monkeypatch.setattr("app.routes.announcements.measure_announcement", _short_measure)
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
