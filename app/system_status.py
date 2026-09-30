from __future__ import annotations

import sys
from pathlib import Path

import psutil

_THERMAL_ROOT = Path("/sys/class/thermal")

# Zone type substrings that indicate a CPU / package / SoC sensor (lowercase).
_CPU_TYPE_HINTS = (
    "cpu",
    "x86_pkg",
    "k10temp",
    "coretemp",
    "soc",
    "pkg",
)

# NSProcessInfoThermalState* integer values (Foundation).
_NS_THERMAL_NOMINAL = 0
_NS_THERMAL_FAIR = 1
_NS_THERMAL_SERIOUS = 2
_NS_THERMAL_CRITICAL = 3


def _read_millidegrees(temp_path: Path) -> float | None:
    try:
        raw = temp_path.read_text(encoding="utf-8").strip()
        return int(raw) / 1000.0
    except (OSError, ValueError):
        return None


def _zone_type(zone_dir: Path) -> str:
    try:
        return zone_dir.joinpath("type").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def _is_cpu_zone(zone_type: str) -> bool:
    return any(hint in zone_type for hint in _CPU_TYPE_HINTS)


def read_cpu_temp_celsius(*, thermal_root: Path | None = None) -> float | None:
    """Return CPU temperature in Celsius from Linux thermal sysfs, or None."""
    root = thermal_root if thermal_root is not None else _THERMAL_ROOT
    if not root.is_dir():
        return None

    zones = sorted(root.glob("thermal_zone*"), key=lambda p: p.name)
    preferred: Path | None = None
    fallback: Path | None = None
    for zone in zones:
        temp_path = zone / "temp"
        if not temp_path.is_file():
            continue
        if fallback is None:
            fallback = temp_path
        if _is_cpu_zone(_zone_type(zone)):
            preferred = temp_path
            break

    target = preferred or fallback
    if target is None:
        return None
    return _read_millidegrees(target)


def read_cpu_percent(*, sample_seconds: float = 0.1) -> float | None:
    """Return aggregate CPU usage percent via psutil, or None."""
    try:
        value = float(psutil.cpu_percent(interval=max(sample_seconds, 0.0)))
    except (OSError, ValueError, TypeError):
        return None
    return max(0.0, min(100.0, value))


def read_memory_percent() -> float | None:
    """Return used memory percent via psutil, or None."""
    try:
        value = float(psutil.virtual_memory().percent)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return max(0.0, min(100.0, value))


def _thermal_state_from_ns(value: int) -> str | None:
    mapping = {
        _NS_THERMAL_NOMINAL: "nominal",
        _NS_THERMAL_FAIR: "fair",
        _NS_THERMAL_SERIOUS: "serious",
        _NS_THERMAL_CRITICAL: "critical",
    }
    return mapping.get(int(value))


def read_thermal_state() -> str | None:
    """Return macOS ProcessInfo thermal state name, or None on other platforms."""
    if sys.platform != "darwin":
        return None
    try:
        from Foundation import NSProcessInfo  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        raw = NSProcessInfo.processInfo().thermalState()
        return _thermal_state_from_ns(int(raw))
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def temp_band(celsius: float | None) -> str:
    """Return cool | warm | hot | na for Linux °C color coding."""
    if celsius is None:
        return "na"
    if celsius < 55:
        return "cool"
    if celsius < 70:
        return "warm"
    return "hot"


def thermal_state_band(state: str | None) -> str:
    """Return UI band for macOS thermal_state (serious != critical)."""
    if state == "nominal":
        return "cool"
    if state == "fair":
        return "warm"
    if state == "serious":
        return "serious"
    if state == "critical":
        return "hot"
    return "na"


def read_system_stats(
    *,
    thermal_root: Path | None = None,
    cpu_sample_seconds: float = 0.1,
) -> dict[str, float | str | None]:
    """Collect CPU temp or thermal state, CPU %, and memory %."""
    celsius = read_cpu_temp_celsius(thermal_root=thermal_root)
    thermal_state = read_thermal_state()
    cpu_percent = read_cpu_percent(sample_seconds=cpu_sample_seconds)
    memory_percent = read_memory_percent()
    return {
        "celsius": round(celsius, 1) if celsius is not None else None,
        "thermal_state": thermal_state,
        "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
        "memory_percent": round(memory_percent, 1) if memory_percent is not None else None,
    }
