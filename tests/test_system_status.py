from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server import app
from app.system_status import (
    read_cpu_percent,
    read_cpu_temp_celsius,
    read_memory_percent,
    read_system_stats,
    temp_band,
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


def test_read_prefers_cpu_typed_zone(tmp_path: Path):
    _make_zone(tmp_path, "thermal_zone0", zone_type="acpitz", millidegrees=30000)
    _make_zone(tmp_path, "thermal_zone1", zone_type="x86_pkg_temp", millidegrees=48200)
    assert read_cpu_temp_celsius(thermal_root=tmp_path) == pytest.approx(48.2)


def test_read_falls_back_to_first_zone(tmp_path: Path):
    _make_zone(tmp_path, "thermal_zone0", zone_type="acpitz", millidegrees=41250)
    assert read_cpu_temp_celsius(thermal_root=tmp_path) == pytest.approx(41.25)


def test_read_missing_sysfs(tmp_path: Path):
    assert read_cpu_temp_celsius(thermal_root=tmp_path / "missing") is None


def test_read_memory_percent(tmp_path: Path):
    path = tmp_path / "meminfo"
    path.write_text(
        "MemTotal:       1000 kB\nMemAvailable:    250 kB\nMemFree:         100 kB\n",
        encoding="utf-8",
    )
    assert read_memory_percent(proc_meminfo=path) == pytest.approx(75.0)


def test_read_memory_percent_without_available(tmp_path: Path):
    path = tmp_path / "meminfo"
    path.write_text(
        "MemTotal:       1000 kB\nMemFree:         100 kB\nBuffers:          50 kB\nCached:          50 kB\n",
        encoding="utf-8",
    )
    assert read_memory_percent(proc_meminfo=path) == pytest.approx(80.0)


def test_read_cpu_percent_from_two_samples(tmp_path: Path, monkeypatch):
    path = tmp_path / "stat"
    path.write_text("cpu  0 0 0 0\n", encoding="utf-8")
    samples = [(800, 1000), (850, 1150)]
    state = {"i": 0}

    def fake_times(_path: Path):
        value = samples[min(state["i"], len(samples) - 1)]
        state["i"] += 1
        return value

    monkeypatch.setattr("app.system_status._read_cpu_times", fake_times)
    monkeypatch.setattr("app.system_status.time.sleep", lambda _s: None)

    # idle_d=50 total_d=150 -> busy = 1 - 50/150
    percent = read_cpu_percent(proc_stat=path, sample_seconds=0.01, reset_state=True)
    assert percent == pytest.approx(100.0 * (1 - 50 / 150))


def test_read_system_stats_combines(tmp_path: Path, monkeypatch):
    thermal = tmp_path / "thermal"
    _make_zone(thermal, "thermal_zone0", zone_type="cpu-thermal", millidegrees=56000)
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal: 2000 kB\nMemAvailable: 1000 kB\n", encoding="utf-8")
    monkeypatch.setattr("app.system_status.read_cpu_percent", lambda **_kwargs: 12.34)

    stats = read_system_stats(
        thermal_root=thermal,
        proc_meminfo=mem,
        reset_cpu_state=True,
    )
    assert stats == {"celsius": 56.0, "cpu_percent": 12.3, "memory_percent": 50.0}


def test_system_stats_api_requires_auth(store, admin_user):
    with TestClient(app) as anon:
        assert anon.get("/api/system/stats").status_code == 401


def test_system_stats_api(client, monkeypatch):
    monkeypatch.setattr(
        "app.server.read_system_stats",
        lambda: {"celsius": 47.6, "cpu_percent": 11.0, "memory_percent": 42.5},
    )
    res = client.get("/api/system/stats")
    assert res.status_code == 200
    assert res.json() == {"celsius": 47.6, "cpu_percent": 11.0, "memory_percent": 42.5}
