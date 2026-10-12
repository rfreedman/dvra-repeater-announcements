"""CM119A / DRA-36M USB HID: PTT (GPIO3) and COS (vol_dn) helpers.

PortAudio playback lives in ``app.dra_playback``. Bench CLI: ``scripts/dra36m_check.py``.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, NamedTuple

log = logging.getLogger("app.cm119")

VENDOR_ID = 0x0D8C
PRODUCT_ID_CM119A = 0x013A

_COS_BIT = {"vol_up": 0x01, "vol_dn": 0x02}


class HidSample(NamedTuple):
    raw: bytes | None
    source: str


def ensure_hid_module():
    if sys.platform == "darwin":
        brew_lib = "/opt/homebrew/lib"
        if os.path.isdir(brew_lib):
            os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", brew_lib)
    try:
        import hid  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "hidapi is required for TTS_RADIO_BACKEND=dra. Install: uv sync "
            "(pip deploy: pip install -r requirements.txt)"
        ) from exc
    if not hasattr(hid, "device"):
        raise RuntimeError(
            "Wrong 'hid' package installed. Run: uv pip uninstall hid && uv sync "
            "(pip deploy: pip uninstall hid && pip install -r requirements.txt)"
        )
    return hid


def normalize_input_report(raw: bytes) -> bytes | None:
    if not raw:
        return None
    if len(raw) >= 5 and raw[0] == 0:
        return bytes(raw[1:5])
    if len(raw) >= 4:
        return bytes(raw[:4])
    return bytes(raw)


def cos_line_busy(
    raw: bytes | None,
    *,
    line: str = "vol_dn",
    active_low: bool = True,
) -> bool | None:
    """True = channel busy (squelch open / receiving), False = idle, None = no HID report."""
    if not raw:
        return None
    if line == "allstar3":
        if len(raw) < 3:
            return None
        return (raw[0] | raw[1] | raw[2]) == 0
    if line not in _COS_BIT:
        line = "vol_dn"
    if len(raw) < 1:
        return None
    mask = _COS_BIT[line]
    bit_set = bool(raw[0] & mask)
    if active_low:
        return not bit_set
    return bit_set


def _cm119_hid_candidates(hid) -> list[dict[str, Any]]:
    candidates: list[dict] = list(hid.enumerate(VENDOR_ID, PRODUCT_ID_CM119A))
    if not candidates:
        candidates = [
            d for d in hid.enumerate(VENDOR_ID, 0) if d.get("vendor_id") == VENDOR_ID
        ]
    if not candidates:
        candidates = [
            d
            for d in hid.enumerate(0, 0)
            if d.get("vendor_id") == VENDOR_ID and d.get("product_id") == PRODUCT_ID_CM119A
        ]
    by_path: dict[bytes, dict] = {}
    for entry in candidates:
        path = entry.get("path")
        if path is None:
            continue
        if entry.get("product_id") != PRODUCT_ID_CM119A:
            continue
        by_path[path] = entry
    items = list(by_path.values())
    preferred = [d for d in items if d.get("interface_number") == 3]
    rest = [d for d in items if d.get("interface_number") != 3]
    return sorted(preferred + rest, key=lambda d: d.get("path") or b"")


def list_cm119_hid_entries(hid) -> list[dict[str, Any]]:
    """One entry per distinct HID path (one DRA per path)."""
    return _cm119_hid_candidates(hid)


def open_cm119_hid_at(hid, path: bytes):
    dev = hid.device()
    dev.open_path(path)
    dev.set_nonblocking(True)
    return dev


def set_gpio3_ptt(dev, keyed: bool) -> None:
    mask = 0x04
    data = 0x04 if keyed else 0x00
    n = dev.write(bytes([0x00, 0x00, mask, data, 0x00]))
    if n is None or n < 0:
        raise OSError(f"CM119 HID PTT write failed (returned {n})")


def read_hid_input_report(dev) -> bytes | None:
    if dev is None:
        return None
    try:
        return normalize_input_report(bytes(dev.get_input_report(0, 5)))
    except (OSError, ValueError):
        return None


def drain_hid_interrupt(dev, *, max_packets: int = 32) -> bytes | None:
    latest: bytes | None = None
    try:
        dev.set_nonblocking(True)
        for _ in range(max_packets):
            data = dev.read(64, timeout_ms=0)
            if not data:
                break
            latest = normalize_input_report(bytes(data))
    except (OSError, ValueError):
        return latest
    return latest


def read_hid_sample(dev, timeout_ms: int = 300) -> HidSample:
    if dev is None:
        return HidSample(None, "not-open")
    ctrl: bytes | None = None
    for attempt in range(3):
        ctrl = read_hid_input_report(dev)
        if ctrl is not None:
            break
        if attempt < 2:
            time.sleep(0.05)

    if ctrl is not None:
        if sys.platform == "darwin":
            return HidSample(ctrl, "ctrl")
        irq = drain_hid_interrupt(dev)
        if irq is not None and irq != ctrl:
            return HidSample(irq, "irq")
        return HidSample(ctrl, "ctrl")

    irq = drain_hid_interrupt(dev)
    if irq is not None:
        return HidSample(irq, "irq")

    block_ms = 100 if sys.platform == "darwin" else min(timeout_ms, 150)
    try:
        dev.set_nonblocking(False)
        data = dev.read(64, timeout_ms=block_ms)
    except (OSError, ValueError):
        data = None
    finally:
        try:
            dev.set_nonblocking(True)
        except (OSError, ValueError):
            pass
    if data:
        blocked = normalize_input_report(bytes(data))
        if blocked is not None:
            return HidSample(blocked, "irq-block")

    ctrl = read_hid_input_report(dev)
    if ctrl is not None:
        return HidSample(ctrl, "ctrl-recover")
    return HidSample(None, "none")


def sample_cm119_hid_once(hid, path: bytes) -> HidSample:
    """Open HID, read one COS sample, close (macOS recovers after COS this way)."""
    for delay in (0.0, 0.04, 0.1, 0.22, 0.45):
        if delay:
            time.sleep(delay)
        try:
            dev = open_cm119_hid_at(hid, path)
        except OSError:
            continue
        try:
            sample = read_hid_sample(dev)
            if sample.raw is not None:
                return sample
        finally:
            try:
                dev.close()
            except OSError:
                pass
            if sys.platform == "darwin":
                time.sleep(0.03)
    return HidSample(None, "open-failed")


def cos_busy_from_raw(
    raw: bytes | None,
    *,
    line: str,
    active_low: bool,
) -> bool | None:
    """Apply vol_dn / allstar3 rules; HID stall while squelch open → busy."""
    busy = cos_line_busy(raw, line=line, active_low=active_low)
    if busy is not None:
        return busy
    if raw is None and line in ("vol_dn", "allstar3"):
        return True
    return None


@dataclass(frozen=True)
class Cm119HidAdapter:
    """One DRA CM119 HID path (PTT + COS)."""

    path: bytes
    label: str

    def read_cos_busy(
        self,
        hid,
        *,
        line: str,
        active_low: bool,
        persistent_dev=None,
    ) -> bool | None:
        if sys.platform == "darwin":
            sample = sample_cm119_hid_once(hid, self.path)
        elif persistent_dev is not None:
            sample = read_hid_sample(persistent_dev)
        else:
            sample = sample_cm119_hid_once(hid, self.path)
        return cos_busy_from_raw(sample.raw, line=line, active_low=active_low)

    def read_cos_tri_state(
        self,
        hid,
        *,
        line: str,
        active_low: bool,
        persistent_dev=None,
    ) -> bool | None:
        """Idle/busy from HID bytes only; ``None`` if no report (does not map stall → busy)."""
        if persistent_dev is not None:
            sample = read_hid_sample(persistent_dev)
        elif sys.platform == "darwin":
            sample = sample_cm119_hid_once(hid, self.path)
        else:
            sample = sample_cm119_hid_once(hid, self.path)
        if sample.raw is None:
            return None
        return cos_line_busy(sample.raw, line=line, active_low=active_low)
