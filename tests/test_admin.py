"""Admin environment history, live settings, and logo / favicon uploads."""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app import config as app_config
from app.admin_config import ConfigState
from app.auth import session_is_idle
from app.config import STATIC_DIR
from app.radio import StubRadio, transmit
from app.server import app
from app.store import AnnouncementStore
from app.users import UserStore
from tests.jsonutil import json_dict


def _state(client: TestClient) -> ConfigState:
    res = client.get("/api/admin/config")
    assert res.status_code == 200
    return ConfigState.model_validate(json_dict(res))


def _values(state: ConfigState) -> dict[str, object]:
    return state.current.values.model_dump()


def _save(
    client: TestClient,
    values: dict[str, object],
    short: str,
    long: str = "",
) -> ConfigState:
    res = client.post(
        "/api/admin/config",
        json={"values": values, "short_description": short, "long_description": long},
    )
    assert res.status_code == 200, res.text
    return ConfigState.model_validate(json_dict(res))


def _png(width: int, height: int, color: tuple[int, int, int] = (20, 80, 40)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_admin_config_is_admin_only(
    store: AnnouncementStore, users: UserStore, readonly_client: TestClient
) -> None:
    _ = store
    _ = users
    assert readonly_client.get("/api/admin/config").status_code == 403
    assert readonly_client.post("/api/admin/config", json={}).status_code == 403
    with TestClient(app) as anon:
        assert anon.get("/api/admin/config").status_code == 401
        logo = anon.get("/branding/logo")
        assert logo.status_code == 200
        assert logo.content == (STATIC_DIR / "logo.png").read_bytes()


def test_save_updates_page_history_and_dotenv(client: TestClient) -> None:
    env_path = Path(os.environ["TTS_DOTENV_PATH"])
    _ = env_path.write_text("# keep me\nTTS_HOST=127.0.0.1\nSTATION_NAME=Old\n", encoding="utf-8")
    state = _state(client)
    values = _values(state)
    values["station_name"] = "North Club"
    saved = _save(client, values, "Weekday nets", "Kept for the winter net")
    assert saved.current.short_description == "Weekday nets"
    assert saved.current.long_description == "Kept for the winter net"
    assert saved.current.saved_at
    page = client.get("/").text
    assert "North Club" in page
    assert "/branding/logo?v=" in page
    assert "{{CONFIG_GENERATION}}" not in page
    assert app_config.STATION_NAME == "North Club"
    text = env_path.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "TTS_HOST=127.0.0.1" in text
    assert "STATION_NAME=North Club" in text
    assert "TTS_SESSION_SECRET" not in text
    assert saved.generation == state.generation + 1
    page_headers = client.get("/").headers
    assert page_headers["x-config-generation"] == "1"
    assert page_headers["cache-control"] == "no-store"


def test_blank_description_does_not_save(client: TestClient) -> None:
    state = _state(client)
    values = _values(state)
    values["station_name"] = "Should Not Stick"
    res = client.post(
        "/api/admin/config",
        json={"values": values, "short_description": "   "},
    )
    assert res.status_code == 400
    assert app_config.STATION_NAME != "Should Not Stick"
    assert _state(client).generation == state.generation


def test_unknown_timezone_is_rejected(client: TestClient, store: AnnouncementStore) -> None:
    state = _state(client)
    before = store.document().settings.timezone
    values = _values(state)
    values["timezone"] = "Not/AZone"
    res = client.post(
        "/api/admin/config",
        json={"values": values, "short_description": "bad zone"},
    )
    assert res.status_code == 422
    assert store.document().settings.timezone == before
    assert app_config.TIMEZONE == before


def test_timezone_save_updates_stored_settings(client: TestClient, store: AnnouncementStore) -> None:
    values = _values(_state(client))
    values["timezone"] = "America/Chicago"
    _ = _save(client, values, "Central time")
    assert store.document().settings.timezone == "America/Chicago"
    assert app_config.TIMEZONE == "America/Chicago"


def test_revert_restores_values_without_a_new_entry(client: TestClient) -> None:
    values = _values(_state(client))
    values["station_name"] = "First Club"
    first = _save(client, values, "First", "The long note")
    values["station_name"] = "Second Club"
    second = _save(client, values, "Second")
    assert len(second.history) == 3
    reverted = client.post("/api/admin/config/revert", json={"id": first.current.id})
    assert reverted.status_code == 200, reverted.text
    body = ConfigState.model_validate(json_dict(reverted))
    assert body.changed is True
    assert len(body.history) == 3
    assert body.current.id == first.current.id
    assert body.current.short_description == "First"
    assert body.current.long_description == "The long note"
    assert app_config.STATION_NAME == "First Club"
    again = client.post("/api/admin/config/revert", json={"id": first.current.id})
    assert again.status_code == 200
    again_body = ConfigState.model_validate(json_dict(again))
    assert again_body.changed is False


def test_ptt_lead_and_session_idle_apply_on_the_next_call(client: TestClient) -> None:
    values = _values(_state(client))
    values["ptt_lead_seconds"] = 2.5
    values["session_idle_seconds"] = 90
    _ = _save(client, values, "Shorter idle")
    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def _play(_chunks: object) -> None:
        return None

    transmit([], radio=StubRadio(), play_fn=_play, sleep_fn=_sleep)
    assert sleeps == [2.5]
    now = time.time()
    assert session_is_idle(now - 120, now) is True


def test_logo_upload_can_become_the_favicon(client: TestClient) -> None:
    wide = _png(128, 64, (12, 40, 90))
    res = client.post(
        "/api/admin/branding/logo",
        files={"file": ("logo.png", wide, "image/png")},
        data={"also_favicon": "true"},
    )
    assert res.status_code == 200, res.text
    logo = client.get("/branding/logo")
    assert logo.content == wide
    icon = client.get("/branding/favicon")
    image = Image.open(io.BytesIO(icon.content))
    assert image.format == "PNG"
    assert image.size == (32, 32)


def test_branding_rejects_bad_files_and_restores_defaults(
    client: TestClient, readonly_client: TestClient
) -> None:
    bad = client.post(
        "/api/admin/branding/logo",
        files={"file": ("logo.png", b"not an image", "image/png")},
    )
    assert bad.status_code == 400
    tiny = client.post(
        "/api/admin/branding/logo",
        files={"file": ("logo.png", _png(8, 8), "image/png")},
    )
    assert tiny.status_code == 400
    assert "64" in str(json_dict(tiny).get("detail", ""))
    uploaded = client.post(
        "/api/admin/branding/logo",
        files={"file": ("logo.png", _png(64, 64), "image/png")},
        data={"also_favicon": "false"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert client.get("/branding/favicon").content == (STATIC_DIR / "favicon.ico").read_bytes()
    removed = client.delete("/api/admin/branding/logo")
    assert removed.status_code == 200
    assert client.get("/branding/logo").content == (STATIC_DIR / "logo.png").read_bytes()
    denied = readonly_client.post(
        "/api/admin/branding/logo",
        files={"file": ("logo.png", _png(64, 64), "image/png")},
    )
    assert denied.status_code == 403


def test_logo_display_size_is_saved_for_the_header(
    client: TestClient, readonly_client: TestClient
) -> None:
    page = client.get("/").text
    assert 'style="--logo-width: 48px; --logo-height: 48px"' in page
    assert "This logo is 256×256 pixels." in page
    assert "The header shows it at 48×48." in page
    assert 'id="logo-display-square" type="checkbox" checked' in page
    assert 'id="logo-display-width-label" for="logo-display-width">Header size</label>' in page
    assert 'id="logo-display-width" type="number" min="16" max="256" step="1" value="48"' in page
    assert 'id="logo-display-height-row" class="voice-row hidden"' in page
    assert 'id="logo-display-height" type="number" min="16" max="256" step="1" value="48"' in page
    assert "{{LOGO_DISPLAY_WIDTH}}" not in page
    assert "{{LOGO_PIXEL_WIDTH}}" not in page
    assert "{{LOGO_SQUARE_CHECKED}}" not in page
    assert "{{LOGO_WIDTH_LABEL}}" not in page
    assert "{{LOGO_HEIGHT_ROW_CLASS}}" not in page
    denied = readonly_client.post("/api/admin/branding/display", json={"width": 40, "height": 96})
    assert denied.status_code == 403
    rejected = client.post("/api/admin/branding/display", json={"width": 8, "height": 48})
    assert rejected.status_code == 400
    assert 'style="--logo-width: 48px; --logo-height: 48px"' in client.get("/").text
    saved = client.post("/api/admin/branding/display", json={"width": 40, "height": 96})
    assert saved.status_code == 200, saved.text
    generation = json_dict(saved)["generation"]
    assert isinstance(generation, int)
    assert generation >= 1
    updated = client.get("/").text
    assert 'id="station-logo"' in updated
    assert 'style="--logo-width: 40px; --logo-height: 96px"' in updated
    assert 'value="40"' in updated
    assert 'value="96"' in updated
    assert 'id="logo-display-square" type="checkbox" checked' not in updated
    assert 'id="logo-display-width-label" for="logo-display-width">Header width</label>' in updated
    assert 'id="logo-display-height-row" class="voice-row"' in updated
    assert 'id="logo-display-height-row" class="voice-row hidden"' not in updated
    removed = client.delete("/api/admin/branding/logo")
    assert removed.status_code == 200
    kept = client.get("/").text
    assert 'style="--logo-width: 40px; --logo-height: 96px"' in kept
    assert 'value="40"' in kept
    assert 'value="96"' in kept
    display_file = Path(os.environ["TTS_BRANDING_DIR"]) / "display.json"
    display_file.unlink()
    uploaded = client.post(
        "/api/admin/branding/logo",
        files={"file": ("logo.png", _png(128, 80), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    sized = client.get("/").text
    assert "This logo is 128×80 pixels." in sized
    assert "The header shows it at 48×48." in sized
    assert 'id="logo-display-square" type="checkbox" checked' in sized
    assert 'id="logo-display-height-row" class="voice-row hidden"' in sized
    assert 'id="logo-display-width" type="number" min="16" max="256" step="1" value="48"' in sized
    assert 'id="logo-display-height" type="number" min="16" max="256" step="1" value="48"' in sized


def test_default_icons_are_generic() -> None:
    svg = (STATIC_DIR / "favicon.svg").read_text(encoding="utf-8")
    assert "DVRA" not in svg
    logo = Image.open(STATIC_DIR / "logo.png").convert("RGBA")
    assert logo.size == (256, 256)
    corner = logo.getpixel((8, 8))
    assert isinstance(corner, tuple)
    assert int(corner[3]) == 0
    gold = logo.getpixel((127, 20))
    assert isinstance(gold, tuple)
    assert gold[:3] == (255, 204, 0)
    icon = Image.open(STATIC_DIR / "favicon.ico")
    assert icon.format == "ICO"
