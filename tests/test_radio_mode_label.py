from __future__ import annotations

from unittest.mock import MagicMock

from app.dra_radio import DraRadio
from app.radio import StubRadio, active_radio_mode_label, radio_mode_label, set_radio


def test_radio_mode_label_stub():
    assert radio_mode_label(StubRadio()) == "stub"


class _FakeAdapter:
    def read_cos_busy(self, _hid, *, line, active_low, persistent_dev=None):
        return False


def test_radio_mode_label_dra():
    radio = DraRadio(
        hid_adapters=[_FakeAdapter()],  # type: ignore[arg-type]
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio_mode_label(radio) == "dra"


def test_radio_mode_label_unknown_fallback():
    assert radio_mode_label(MagicMock()) == "magicmock"


def test_active_radio_mode_label_uses_registered_radio():
    set_radio(StubRadio())
    assert active_radio_mode_label() == "stub"
