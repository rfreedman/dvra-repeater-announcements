"""COS idle credit must accrue on decoded idle bytes, not stall-as-busy."""
from __future__ import annotations

import time

from app.dra_radio import DraRadio


class _TriAdapter:
    label = "test"
    path = b"\x01"

    def __init__(self, values: list[bool | None]) -> None:
        self._values = list(values)

    def read_cos_tri_state(self, _hid, *, line, active_low):
        if not self._values:
            return None
        return self._values.pop(0)


def test_idle_credit_survives_missed_hid_report():
    radio = DraRadio(
        hid_adapters=[_TriAdapter([False, None, None])],  # type: ignore[list-item]
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio.refresh_cos() is False
    time.sleep(0.05)
    assert radio.refresh_cos() is False
    assert radio.continuous_idle_seconds() >= 0.04


def test_single_busy_blip_does_not_reset_idle_credit():
    radio = DraRadio(
        hid_adapters=[_TriAdapter([True])],  # type: ignore[list-item]
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    with radio._lock:
        radio._idle_since_mono = time.monotonic() - 12.0
        radio._last_cos_busy = False
    assert radio.refresh_cos() is True
    assert radio.continuous_idle_seconds() >= 11.0


def test_stall_bytes_do_not_start_idle():
    radio = DraRadio(
        hid_adapters=[_TriAdapter([None, None])],  # type: ignore[list-item]
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio.refresh_cos() is True
    assert radio.continuous_idle_seconds() == 0.0
