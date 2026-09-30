from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server import app
from app.system_status import read_cpu_temp_celsius, temp_band


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


def test_read_prefers_cpu_typed_zone(tmp_path: Path):
    _make_zone(tmp_path, "thermal_zone0", zone_type="acpitz", millidegrees=30000)
    _make_zone(tmp_path, "thermal_zone1", zone_type="x86_pkg_temp", millidegrees=48200)
    assert read_cpu_temp_celsius(thermal_root=tmp_path) == pytest.approx(48.2)


def test_read_falls_back_to_first_zone(tmp_path: Path):
    _make_zone(tmp_path, "thermal_zone0", zone_type="acpitz", millidegrees=41250)
    assert read_cpu_temp_celsius(thermal_root=tmp_path) == pytest.approx(41.25)


def test_read_missing_sysfs(tmp_path: Path):
    assert read_cpu_temp_celsius(thermal_root=tmp_path / "missing") is None


def test_cpu_temp_api_requires_auth(store, admin_user):
    with TestClient(app) as anon:
        assert anon.get("/api/system/cpu-temp").status_code == 401


def test_cpu_temp_api_available(client, monkeypatch):
    monkeypatch.setattr("app.server.read_cpu_temp_celsius", lambda: 47.6)
    res = client.get("/api/system/cpu-temp")
    assert res.status_code == 200
    assert res.json() == {"celsius": 47.6, "available": True}


def test_cpu_temp_api_unavailable(client, monkeypatch):
    monkeypatch.setattr("app.server.read_cpu_temp_celsius", lambda: None)
    res = client.get("/api/system/cpu-temp")
    assert res.status_code == 200
    assert res.json() == {"celsius": None, "available": False}
