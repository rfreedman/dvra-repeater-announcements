from __future__ import annotations

import logging
from typing import cast

from app.cm119 import HidDeviceEntry, HidModule
from app.dra_radio import DraRadio


import pytest
def test_from_config_logs_dual_pairing_without_dual_nuc_wording(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    paths = [b"\x01", b"\x02"]

    def fake_hid_module() -> HidModule:
        return cast(HidModule, object())

    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [
            HidDeviceEntry(path=p, vendor_id=0, product_id=0) for p in paths
        ]

    def fake_audio_indices(*, device_specs: list[str] | None = None) -> list[int]:
        _ = device_specs
        return [3, 4]

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", fake_hid_module)
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", fake_entries)
    monkeypatch.setattr("app.dra_portaudio.resolve_dra_audio_indices", fake_audio_indices)
    monkeypatch.setattr("app.dra_radio.DRA_HID_PATH_BYTES", None)

    caplog.set_level(logging.INFO, logger="app.dra_radio")
    radio = DraRadio.from_config()
    assert radio.hid_count == 2
    assert radio.audio_device_indices == [3, 4]
    joined = caplog.text
    assert "dual-NUC" not in joined.casefold()
    assert "DRA paired on one host: 2 CM119 HID" in joined


def test_from_config_mismatch_warning_mentions_unset_hid_path(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def fake_hid_module() -> HidModule:
        return cast(HidModule, object())

    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [HidDeviceEntry(path=b"\x01", vendor_id=0, product_id=0)]

    def fake_audio_indices(*, device_specs: list[str] | None = None) -> list[int]:
        _ = device_specs
        return [3, 4]

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", fake_hid_module)
    monkeypatch.setattr(
        "app.dra_radio.list_cm119_hid_entries",
        fake_entries,
    )
    monkeypatch.setattr("app.dra_portaudio.resolve_dra_audio_indices", fake_audio_indices)
    monkeypatch.setattr("app.dra_radio.DRA_HID_PATH_BYTES", None)

    caplog.set_level(logging.WARNING, logger="app.dra_radio")
    radio = DraRadio.from_config()
    assert radio.hid_count == 1
    assert radio.audio_device_indices == [3]
    text = caplog.text
    assert "dual-NUC" not in text.casefold()
    assert "Unset TTS_DRA_HID_PATH" in text
