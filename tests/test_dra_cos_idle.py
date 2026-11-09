"""COS idle credit must accrue on decoded idle bytes, not stall-as-busy."""
from __future__ import annotations

import time
from typing import cast

from app.cm119 import Cm119HidAdapter, HidModule
from app.dra_radio import DraRadio


class _TriAdapter:
    label: str = "test"
    path: bytes = b"\x01"
    _values: list[bool | None]

    def __init__(self, values: list[bool | None]) -> None:
        self._values = list(values)

    def read_cos_tri_state(
        self, _hid: HidModule, *, line: str, active_low: bool
    ) -> bool | None:
        _ = line
        _ = active_low
        if not self._values:
            return None
        return self._values.pop(0)


def test_idle_credit_survives_missed_hid_report() -> None:
    radio = DraRadio(
        hid_adapters=cast(
            list[Cm119HidAdapter],
            [_TriAdapter([False, None, None])],
        ),
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio.refresh_cos() is False
    time.sleep(0.05)
    assert radio.refresh_cos() is False
    assert radio.continuous_idle_seconds() >= 0.04


def test_single_busy_sample_resets_idle_credit() -> None:
    """A kerchunk shorter than two polls shows up as exactly one busy sample."""
    radio = DraRadio(
        hid_adapters=cast(list[Cm119HidAdapter], [_TriAdapter([True])]),
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    radio.set_idle_credit(12.0)
    assert radio.refresh_cos() is True
    assert radio.continuous_idle_seconds() == 0.0


def test_stall_bytes_do_not_start_idle() -> None:
    radio = DraRadio(
        hid_adapters=cast(list[Cm119HidAdapter], [_TriAdapter([None, None])]),
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio.refresh_cos() is True
    assert radio.continuous_idle_seconds() == 0.0
