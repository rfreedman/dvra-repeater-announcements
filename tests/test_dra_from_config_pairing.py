from __future__ import annotations

import logging

from app.dra_radio import DraRadio


def test_from_config_logs_dual_pairing_without_dual_nuc_wording(monkeypatch, caplog):
    paths = [b"\x01", b"\x02"]

    def fake_entries(_hid):
        return [{"path": p} for p in paths]

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", lambda: object())
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", fake_entries)
    monkeypatch.setattr("app.dra_radio.resolve_dra_audio_indices", lambda **_: [3, 4])
    monkeypatch.setattr("app.dra_radio.DRA_HID_PATH_BYTES", None)

    caplog.set_level(logging.INFO, logger="app.dra_radio")
    radio = DraRadio.from_config()
    assert radio.hid_count == 2
    assert radio.audio_device_indices == [3, 4]
    joined = caplog.text
    assert "dual-NUC" not in joined.casefold()
    assert "DRA paired on one host: 2 CM119 HID" in joined


def test_from_config_mismatch_warning_mentions_unset_hid_path(monkeypatch, caplog):
    monkeypatch.setattr("app.dra_radio.ensure_hid_module", lambda: object())
    monkeypatch.setattr(
        "app.dra_radio.list_cm119_hid_entries",
        lambda _hid: [{"path": b"\x01"}],
    )
    monkeypatch.setattr("app.dra_radio.resolve_dra_audio_indices", lambda **_: [3, 4])
    monkeypatch.setattr("app.dra_radio.DRA_HID_PATH_BYTES", None)

    caplog.set_level(logging.WARNING, logger="app.dra_radio")
    radio = DraRadio.from_config()
    assert radio.hid_count == 1
    assert radio.audio_device_indices == [3]
    text = caplog.text
    assert "dual-NUC" not in text.casefold()
    assert "Unset TTS_DRA_HID_PATH" in text
