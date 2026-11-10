from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Must be set before any app module is imported (app.config reads them at import).
_ = os.environ.setdefault("TTS_SESSION_SECRET", "test-session-secret-for-pytest")
_ = os.environ.setdefault("STATION_CALL", "N0TST")
_ = os.environ.setdefault("STATION_NAME", "Test Radio Club")
os.environ["TTS_SKIP_DOTENV"] = "1"

from app.auth import hash_password
from app.models import Announcement, TtsAnnouncement
from app.radio import StubRadio, set_radio
from app.server import app
from app.spoken_length import apply_played_length
from app.store import AnnouncementStore, set_store
from app.users import User, UserStore, set_user_store

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


def _fake_sounddevice_query_devices(
    device: int | str | None = None, kind: str | None = None
) -> object:
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
def restore_operator_settings() -> Iterator[None]:
    """Admin saves publish process-wide settings. Put them back after each test."""
    import app.admin_config as admin_config
    from app.auth import login_limiter
    from app.config import assign_live_settings
    from app.scheduler import PromptBackgroundScheduler
    from app.scheduler_instance import get_scheduler

    snapshot = admin_config.current_values()
    generation = admin_config.config_generation()
    yield
    assign_live_settings(
        station_call=snapshot.station_call,
        station_name=snapshot.station_name,
        app_title=snapshot.app_title,
        default_voice=snapshot.default_voice,
        speed=snapshot.speed,
        sentence_pause=snapshot.sentence_pause,
        ptt_lead_seconds=snapshot.ptt_lead_seconds,
        busy_retry_seconds=snapshot.busy_retry_seconds,
        busy_give_up_seconds=snapshot.busy_give_up_seconds,
        max_text_chars=snapshot.max_text_chars,
        piper_qualities=snapshot.piper_qualities,
        timezone=snapshot.timezone,
        slot_lookahead_hours=snapshot.slot_lookahead_hours,
        scheduler_max_wait_seconds=snapshot.scheduler_max_wait_seconds,
        session_idle_seconds=snapshot.session_idle_seconds,
        login_max_failures=snapshot.login_max_failures,
        login_window_seconds=snapshot.login_window_seconds,
        trigger_now=snapshot.trigger_now,
        fire_verbose_log=snapshot.fire_verbose_log,
    )
    login_limiter.max_failures = snapshot.login_max_failures
    login_limiter.window_seconds = snapshot.login_window_seconds
    scheduler = get_scheduler()
    if isinstance(scheduler, PromptBackgroundScheduler):
        scheduler.max_wait_seconds = max(0.5, snapshot.scheduler_max_wait_seconds)
    admin_config.set_config_generation(generation)


@pytest.fixture(autouse=True)
def isolate_admin_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin saves must not touch the developer .env, history, or branding files."""
    monkeypatch.setenv("TTS_DOTENV_PATH", str(tmp_path / "runtime.env"))
    monkeypatch.setenv("TTS_CONFIG_HISTORY_PATH", str(tmp_path / "config_history.json"))
    monkeypatch.setenv("TTS_BRANDING_DIR", str(tmp_path / "branding"))


@pytest.fixture(autouse=True)
def headless_portaudio_on_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headless GitHub runners expose PortAudio without usable device indices."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    monkeypatch.setattr("sounddevice.query_devices", _fake_sounddevice_query_devices)


@pytest.fixture
def stub_dra_playback_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standard mocks for ``play_pcm_chunks_to_dra_devices`` on Linux/CI."""

    def _unmute_indices(_indices: object) -> None:
        return None

    def _unmute_pulse(_index: object) -> None:
        return None

    def _samplerate(_index: object) -> int:
        return 44100

    def _use_aplay() -> bool:
        return False

    monkeypatch.setattr(
        "app.dra_alsa_unmute.sync_cm119_speaker_unmute_for_indices",
        _unmute_indices,
    )
    monkeypatch.setattr(
        "app.dra_pulse_mixer.sync_pulse_sink_unmute_for_portaudio_name",
        _unmute_pulse,
    )
    monkeypatch.setattr("app.dra_playback.dra_device_samplerate", _samplerate)
    monkeypatch.setattr("app.dra_playback._linux_use_aplay", _use_aplay)


@pytest.fixture(autouse=True)
def fire_wall_clock_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep give-up / defer logic aligned with frozen ``deps.now`` in unit tests."""
    import app.fire as fire_mod
    from app.fire_types import FireDeps

    def _wall_now(deps: FireDeps) -> object:
        return deps.now

    monkeypatch.setattr(fire_mod, "_wall_now", _wall_now)


@pytest.fixture(autouse=True)
def no_voice_preload(monkeypatch: pytest.MonkeyPatch) -> None:
    def _preload(self: object) -> list[str]:
        _ = self
        return []

    monkeypatch.setattr("app.registry.EngineRegistry.preload", _preload)


@pytest.fixture(autouse=True)
def keep_test_radio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Server startup must not replace the radio a test registered (``radio`` fixture)."""
    def _skip_radio_init() -> None:
        return None

    monkeypatch.setattr("app.server.init_radio_at_startup", _skip_radio_init)
    yield
    set_radio(None)


@pytest.fixture(autouse=True)
def reset_login_limiter() -> Iterator[None]:
    from app.auth import login_limiter

    login_limiter.reset()
    yield
    login_limiter.reset()


@pytest.fixture(autouse=True)
def no_newsline_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scheduler startup and settings saves must not fetch the Newsline feed."""
    def _no_poll() -> None:
        return None

    def _no_arm(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.newsline.poll.install_news_poll", _no_poll)
    monkeypatch.setattr("app.newsline.poll.arm_next_poll", _no_arm)


def _short_measure(announcement: Announcement, timeout: int) -> Announcement:
    if announcement.kind != "tts":
        return announcement
    assert isinstance(announcement, TtsAnnouncement)
    return apply_played_length(announcement, 1.0, timeout)


@pytest.fixture(autouse=True)
def no_pcm_warm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    from app.pcm_cache import PcmCache, set_pcm_cache

    set_pcm_cache(PcmCache(tmp_path / "pcm-cache"))
    def _skip_warm(announcement: Announcement) -> None:
        _ = announcement
        return None

    def _skip_warm_all(announcements: object = None) -> None:
        _ = announcements
        return None

    monkeypatch.setattr("app.routes.announcements.warm_announcement", _skip_warm)
    monkeypatch.setattr("app.routes.announcements.measure_announcement", _short_measure)
    monkeypatch.setattr("app.server.warm_all", _skip_warm_all)
    yield
    set_pcm_cache(None)


@pytest.fixture(autouse=True)
def isolate_phoneme_dictionary(tmp_path: Path) -> Iterator[None]:
    """Keep tests off the operator's phonemes.json."""
    from app.phoneme_dict import PhonemeDictionary, set_phoneme_dictionary

    set_phoneme_dictionary(PhonemeDictionary(tmp_path / "phonemes.json"))
    yield
    set_phoneme_dictionary(None)


@pytest.fixture
def store(tmp_path: Path, isolate_phoneme_dictionary: None) -> Iterator[AnnouncementStore]:
    _ = isolate_phoneme_dictionary
    path = tmp_path / "announcements.json"
    item = AnnouncementStore(path)
    set_store(item)
    yield item
    set_store(None)


@pytest.fixture
def users(tmp_path: Path) -> Iterator[UserStore]:
    path = tmp_path / "users.json"
    item = UserStore(path)
    set_user_store(item)
    yield item
    set_user_store(None)


@pytest.fixture
def admin_user(users: UserStore) -> User:
    return users.create(
        username=ADMIN_USERNAME,
        password_hash=hash_password(ADMIN_PASSWORD),
        role="admin",
    )


@pytest.fixture
def readonly_user(users: UserStore) -> User:
    return users.create(
        username="viewer",
        password_hash=hash_password(ADMIN_PASSWORD),
        role="readonly",
    )


@pytest.fixture
def client(store: AnnouncementStore, admin_user: User) -> Iterator[TestClient]:
    _ = store
    _ = admin_user
    with TestClient(app) as test_client:
        res = test_client.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert res.status_code == 200
        yield test_client


@pytest.fixture
def readonly_client(
    store: AnnouncementStore, admin_user: User, readonly_user: User
) -> Iterator[TestClient]:
    _ = store
    _ = admin_user
    _ = readonly_user
    with TestClient(app) as test_client:
        res = test_client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": ADMIN_PASSWORD},
        )
        assert res.status_code == 200
        yield test_client


@pytest.fixture
def radio() -> Iterator[StubRadio]:
    item = StubRadio()
    set_radio(item)
    yield item
    set_radio(None)
