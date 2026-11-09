from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.server import app
from app.store import AnnouncementStore
from app.users import User
from app.system_status import (
    thermal_state_from_ns,
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
    _ = (zone / "type").write_text(f"{zone_type}\n", encoding="utf-8")
    _ = (zone / "temp").write_text(f"{millidegrees}\n", encoding="utf-8")


def test_temp_band() -> None:
    assert temp_band(None) == "na"
    assert temp_band(54.9) == "cool"
    assert temp_band(55) == "warm"
    assert temp_band(69.9) == "warm"
    assert temp_band(70) == "hot"


def test_thermal_state_band_distinguishes_serious_and_critical() -> None:
    assert thermal_state_band("nominal") == "cool"
    assert thermal_state_band("fair") == "warm"
    assert thermal_state_band("serious") == "serious"
    assert thermal_state_band("critical") == "hot"
    assert thermal_state_band(None) == "na"


def testthermal_state_from_ns() -> None:
    assert thermal_state_from_ns(0) == "nominal"
    assert thermal_state_from_ns(1) == "fair"
    assert thermal_state_from_ns(2) == "serious"
    assert thermal_state_from_ns(3) == "critical"
    assert thermal_state_from_ns(99) is None


def test_read_prefers_cpu_typed_zone(tmp_path: Path) -> None:
    _make_zone(tmp_path, "thermal_zone0", zone_type="acpitz", millidegrees=30000)
    _make_zone(tmp_path, "thermal_zone1", zone_type="x86_pkg_temp", millidegrees=48200)
    assert read_cpu_temp_celsius(thermal_root=tmp_path) == pytest.approx(48.2)


def test_read_falls_back_to_first_zone(tmp_path: Path) -> None:
    _make_zone(tmp_path, "thermal_zone0", zone_type="acpitz", millidegrees=41250)
    assert read_cpu_temp_celsius(thermal_root=tmp_path) == pytest.approx(41.25)


def test_read_missing_sysfs(tmp_path: Path) -> None:
    assert read_cpu_temp_celsius(thermal_root=tmp_path / "missing") is None


def test_read_cpu_percent_psutil_primes_then_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_cpu_percent(interval: float | None = None) -> float:
        calls.append(interval)
        return 0.0 if len(calls) == 1 else 17.6

    monkeypatch.setattr("app.system_status.psutil.cpu_percent", fake_cpu_percent)
    assert read_cpu_percent(reset_state=True) is None
    assert read_cpu_percent() == pytest.approx(17.6)
    assert calls == [None, None]


def test_read_memory_percent_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_virtual_memory() -> SimpleNamespace:
        return SimpleNamespace(percent=42.5)

    monkeypatch.setattr(
        "app.system_status.psutil.virtual_memory",
        fake_virtual_memory,
    )
    assert read_memory_percent() == pytest.approx(42.5)


def test_read_thermal_state_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.system_status.sys.platform", "linux")
    assert read_thermal_state() is None


def test_read_system_stats_combines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thermal = tmp_path / "thermal"
    _make_zone(thermal, "thermal_zone0", zone_type="cpu-thermal", millidegrees=56000)
    def fake_cpu_percent(*, reset_state: bool = False) -> float:
        _ = reset_state
        return 12.34

    def fake_memory_percent() -> float:
        return 50.0

    def fake_thermal_state() -> None:
        return None

    monkeypatch.setattr("app.system_status.read_cpu_percent", fake_cpu_percent)
    monkeypatch.setattr("app.system_status.read_memory_percent", fake_memory_percent)
    monkeypatch.setattr("app.system_status.read_thermal_state", fake_thermal_state)

    stats = read_system_stats(thermal_root=thermal)
    assert stats == {
        "celsius": 56.0,
        "thermal_state": None,
        "cpu_percent": 12.3,
        "memory_percent": 50.0,
    }


def test_read_system_stats_macos_thermal(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_cpu_temp(*, thermal_root: Path | None = None) -> None:
        _ = thermal_root
        return None

    def fake_thermal_state() -> str:
        return "fair"

    def fake_cpu_percent(*, reset_state: bool = False) -> float:
        _ = reset_state
        return 8.0

    def fake_memory_percent() -> float:
        return 33.0

    monkeypatch.setattr("app.system_status.read_cpu_temp_celsius", fake_cpu_temp)
    monkeypatch.setattr("app.system_status.read_thermal_state", fake_thermal_state)
    monkeypatch.setattr("app.system_status.read_cpu_percent", fake_cpu_percent)
    monkeypatch.setattr("app.system_status.read_memory_percent", fake_memory_percent)

    stats = read_system_stats()
    assert stats == {
        "celsius": None,
        "thermal_state": "fair",
        "cpu_percent": 8.0,
        "memory_percent": 33.0,
    }


def test_system_stats_api_requires_auth(store: AnnouncementStore, admin_user: User) -> None:
    _ = store
    _ = admin_user
    with TestClient(app) as anon:
        assert anon.get("/api/system/stats").status_code == 401


def test_system_stats_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_stats() -> dict[str, float | str | None]:
        return {
            "celsius": 47.6,
            "thermal_state": None,
            "cpu_percent": 11.0,
            "memory_percent": 42.5,
        }

    monkeypatch.setattr(
        "app.server.read_system_stats",
        fake_stats,
    )
    res = client.get("/api/system/stats")
    assert res.status_code == 200
    assert res.json() == {
        "celsius": 47.6,
        "thermal_state": None,
        "cpu_percent": 11.0,
        "memory_percent": 42.5,
    }
