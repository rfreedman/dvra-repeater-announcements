from __future__ import annotations

from pathlib import Path

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


def temp_band(celsius: float | None) -> str:
    """Return cool | warm | hot | na for UI color coding."""
    if celsius is None:
        return "na"
    if celsius < 55:
        return "cool"
    if celsius < 70:
        return "warm"
    return "hot"
