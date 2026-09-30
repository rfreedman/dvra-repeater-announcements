from __future__ import annotations

import time
from pathlib import Path

_THERMAL_ROOT = Path("/sys/class/thermal")
_PROC_STAT = Path("/proc/stat")
_PROC_MEMINFO = Path("/proc/meminfo")

# Zone type substrings that indicate a CPU / package / SoC sensor (lowercase).
_CPU_TYPE_HINTS = (
    "cpu",
    "x86_pkg",
    "k10temp",
    "coretemp",
    "soc",
    "pkg",
)

# Previous (idle, total) jiffies from /proc/stat for delta CPU %.
_prev_cpu_times: tuple[int, int] | None = None


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


def _read_cpu_times(proc_stat: Path) -> tuple[int, int] | None:
    """Return (idle_jiffies, total_jiffies) from the aggregate cpu line."""
    try:
        first = proc_stat.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first.split()
    if not parts or parts[0] != "cpu":
        return None
    try:
        values = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
    total = sum(values)
    return idle, total


def read_cpu_percent(
    *,
    proc_stat: Path | None = None,
    sample_seconds: float = 0.1,
    reset_state: bool = False,
) -> float | None:
    """Return aggregate CPU usage percent from /proc/stat, or None."""
    global _prev_cpu_times
    path = proc_stat if proc_stat is not None else _PROC_STAT
    if reset_state:
        _prev_cpu_times = None
    if not path.is_file():
        return None

    first = _read_cpu_times(path)
    if first is None:
        return None

    previous = _prev_cpu_times
    if previous is None:
        time.sleep(max(sample_seconds, 0.0))
        second = _read_cpu_times(path)
        if second is None:
            return None
        _prev_cpu_times = second
        idle_d = second[0] - first[0]
        total_d = second[1] - first[1]
    else:
        _prev_cpu_times = first
        idle_d = first[0] - previous[0]
        total_d = first[1] - previous[1]

    if total_d <= 0:
        return None
    busy = 1.0 - (idle_d / total_d)
    return max(0.0, min(100.0, busy * 100.0))


def _parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0])
        except ValueError:
            continue
    return values


def read_memory_percent(*, proc_meminfo: Path | None = None) -> float | None:
    """Return used memory percent from /proc/meminfo (via MemAvailable), or None."""
    path = proc_meminfo if proc_meminfo is not None else _PROC_MEMINFO
    if not path.is_file():
        return None
    try:
        values = _parse_meminfo(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    total = values.get("MemTotal")
    if not total or total <= 0:
        return None
    available = values.get("MemAvailable")
    if available is None:
        free = values.get("MemFree", 0)
        buffers = values.get("Buffers", 0)
        cached = values.get("Cached", 0)
        available = free + buffers + cached
    used = total - available
    return max(0.0, min(100.0, (used / total) * 100.0))


def temp_band(celsius: float | None) -> str:
    """Return cool | warm | hot | na for UI color coding."""
    if celsius is None:
        return "na"
    if celsius < 55:
        return "cool"
    if celsius < 70:
        return "warm"
    return "hot"


def read_system_stats(
    *,
    thermal_root: Path | None = None,
    proc_stat: Path | None = None,
    proc_meminfo: Path | None = None,
    cpu_sample_seconds: float = 0.1,
    reset_cpu_state: bool = False,
) -> dict[str, float | None]:
    """Collect CPU temp, CPU %, and memory % (null when unavailable)."""
    celsius = read_cpu_temp_celsius(thermal_root=thermal_root)
    cpu_percent = read_cpu_percent(
        proc_stat=proc_stat,
        sample_seconds=cpu_sample_seconds,
        reset_state=reset_cpu_state,
    )
    memory_percent = read_memory_percent(proc_meminfo=proc_meminfo)
    return {
        "celsius": round(celsius, 1) if celsius is not None else None,
        "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
        "memory_percent": round(memory_percent, 1) if memory_percent is not None else None,
    }
