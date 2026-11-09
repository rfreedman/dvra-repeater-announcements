from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

from app.cm119 import Cm119HidAdapter, HidDevice, HidModule
from app.dra_radio import DraRadio
from app.radio import StubRadio, active_radio_mode_label, radio_mode_label, set_radio


def test_radio_mode_label_stub() -> None:
    assert radio_mode_label(StubRadio()) == "stub"


class _FakeAdapter:
    def read_cos_busy(
        self,
        _hid: HidModule,
        *,
        line: str,
        active_low: bool,
        persistent_dev: HidDevice | None = None,
    ) -> bool:
        _ = line
        _ = active_low
        _ = persistent_dev
        return False


def test_radio_mode_label_dra() -> None:
    radio = DraRadio(
        hid_adapters=cast(list[Cm119HidAdapter], [_FakeAdapter()]),
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio_mode_label(radio) == "dra"


def test_radio_mode_label_unknown_fallback() -> None:
    assert radio_mode_label(MagicMock()) == "magicmock"


def test_active_radio_mode_label_uses_registered_radio() -> None:
    set_radio(StubRadio())
    assert active_radio_mode_label() == "stub"
