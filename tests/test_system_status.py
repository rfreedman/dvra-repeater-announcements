from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.server import app
from app.system_status import (
    _thermal_state_from_ns,
    read_cpu_percent,
    read_cpu_temp_celsius,
    read_memory_percent,
    read_system_stats,
    read_thermal_state,
    temp_band,
    thermal_state_band,
)


def _make_zone(root: Path, name: str, *, zone_type: str, millidegrees: int) -> None:
    zone = root / name
    zone.mkdir(parents=True)
    (zone / "type").write_text(f"{zone_type}\n", encoding="utf-8")
    (zone / "temp").write_text(f"{millidegrees}\n", encoding="utf-8")


def test_temp_band():
    assert temp_band(None) == "na"
    assert temp_band(54.9) == "cool"
    assert temp_band(55) == "warm"
    assert temp_band(69.9) == "warm"
    assert temp_band(70) == "hot"


def test_thermal_state_band_distinguishes_serious_and_critical():
    assert thermal_state_band("nominal") == "cool"
    assert thermal_state_band("fair") == "warm"
    assert thermal_state_band("serious") == "serious"
    assert thermal_state_band("critical") == "hot"
    assert thermal_state_band(None) == "na"


def test_thermal_state_from_ns():
    assert _thermal_state_from_ns(0) == "nominal"
    assert _thermal_state_from_ns(1) == "fair"
    assert _thermal_state_from_ns(2) == "serious"
    assert _thermal_state_from_ns(3) == "critical"
    assert _thermal_state_from_ns(99) is None


def test_read_prefers_cpu_typed_zone(tmp_path: Path):
    _make_zone(tmp_path, "thermal_zone0", zone_type="acpitz", millidegrees=30000)
    _make_zone(tmp_path, "thermal_zone1", zone_type="x86_pkg_temp", millidegrees=48200)
    assert read_cpu_temp_celsius(thermal_root=tmp_path) == pytest.approx(48.2)


def test_read_falls_back_to_first_zone(tmp_path: Path):
    _make_zone(tmp_path, "thermal_zone0", zone_type="acpitz", millidegrees=41250)
    assert read_cpu_temp_celsius(thermal_root=tmp_path) == pytest.approx(41.25)


def test_read_missing_sysfs(tmp_path: Path):
    assert read_cpu_temp_celsius(thermal_root=tmp_path / "missing") is None


def test_read_cpu_percent_psutil(monkeypatch):
    monkeypatch.setattr("app.system_status.psutil.cpu_percent", lambda interval=0.1: 17.6)
    assert read_cpu_percent(sample_seconds=0.01) == pytest.approx(17.6)


def test_read_memory_percent_psutil(monkeypatch):
    monkeypatch.setattr(
        "app.system_status.psutil.virtual_memory",
        lambda: SimpleNamespace(percent=42.5),
    )
    assert read_memory_percent() == pytest.approx(42.5)


def test_read_thermal_state_non_darwin(monkeypatch):
    monkeypatch.setattr("app.system_status.sys.platform", "linux")
    assert read_thermal_state() is None


def test_read_system_stats_combines(tmp_path: Path, monkeypatch):
    thermal = tmp_path / "thermal"
    _make_zone(thermal, "thermal_zone0", zone_type="cpu-thermal", millidegrees=56000)
    monkeypatch.setattr("app.system_status.read_cpu_percent", lambda **_kwargs: 12.34)
    monkeypatch.setattr("app.system_status.read_memory_percent", lambda: 50.0)
    monkeypatch.setattr("app.system_status.read_thermal_state", lambda: None)

    stats = read_system_stats(thermal_root=thermal, cpu_sample_seconds=0.0)
    assert stats == {
        "celsius": 56.0,
        "thermal_state": None,
        "cpu_percent": 12.3,
        "memory_percent": 50.0,
    }


def test_read_system_stats_macos_thermal(monkeypatch):
    monkeypatch.setattr("app.system_status.read_cpu_temp_celsius", lambda **_kwargs: None)
    monkeypatch.setattr("app.system_status.read_thermal_state", lambda: "fair")
    monkeypatch.setattr("app.system_status.read_cpu_percent", lambda **_kwargs: 8.0)
    monkeypatch.setattr("app.system_status.read_memory_percent", lambda: 33.0)

    stats = read_system_stats(cpu_sample_seconds=0.0)
    assert stats == {
        "celsius": None,
        "thermal_state": "fair",
        "cpu_percent": 8.0,
        "memory_percent": 33.0,
    }


def test_system_stats_api_requires_auth(store, admin_user):
    with TestClient(app) as anon:
        assert anon.get("/api/system/stats").status_code == 401


def test_system_stats_api(client, monkeypatch):
    monkeypatch.setattr(
        "app.server.read_system_stats",
        lambda: {
            "celsius": 47.6,
            "thermal_state": None,
            "cpu_percent": 11.0,
            "memory_percent": 42.5,
        },
    )
    res = client.get("/api/system/stats")
    assert res.status_code == 200
    assert res.json() == {
        "celsius": 47.6,
        "thermal_state": None,
        "cpu_percent": 11.0,
        "memory_percent": 42.5,
    }
