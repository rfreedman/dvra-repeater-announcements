"""USB replug: refresh CM119 HID paths when ALSA card number is unchanged."""

from __future__ import annotations

from typing import cast

from app.cm119 import Cm119HidAdapter, HidDeviceEntry, HidModule
from app.dra_alsa_mixer import apply_cm119_replug_recovery, alsa_watch
from app.dra_radio import DraRadio


import pytest


def _fake_hid_module() -> HidModule:
    return cast(HidModule, object())


def test_sync_hid_adapters_updates_paths_and_clears_idle_credit(monkeypatch: pytest.MonkeyPatch) -> None:
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"old-path", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    radio.set_idle_credit(100.0)

    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [{"path": b"new-path", "vendor_id": 0, "product_id": 0}]

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", _fake_hid_module)
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", fake_entries)
    assert radio.sync_hid_adapters_if_replugged() is True
    assert radio.hid_adapter_path() == b"new-path"
    assert radio.continuous_idle_seconds() == 0.0
    assert radio.last_cos_busy is None


def test_apply_replug_recovery_hid_only_unmutes_speaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    alsa_watch.last_unmuted_signature = (1,)
    def fake_signature(limit: int) -> tuple[int, ...]:
        _ = limit
        return (1,)

    monkeypatch.setattr(
        "app.dra_alsa_unmute.cm119_alsa_card_signature", fake_signature
    )
    unmuted: list[list[int]] = []
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"old", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [{"path": b"new", "vendor_id": 0, "product_id": 0}]

    def unmute_enabled() -> bool:
        return True

    def fake_cards(_radio: object) -> list[int]:
        return [1]

    def unmute_cards(cards: list[int], level: str) -> bool:
        _ = level
        unmuted.append(cards)
        return True

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", _fake_hid_module)
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", fake_entries)
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", unmute_enabled)
    monkeypatch.setattr(
        "app.dra_alsa_mixer.cm119_alsa_cards_for_radio",
        fake_cards,
    )
    monkeypatch.setattr(
        "app.dra_alsa_mixer.try_unmute_cm119_cards",
        unmute_cards,
    )
    apply_cm119_replug_recovery(radio)
    assert radio.hid_adapter_path() == b"new"
    assert unmuted == [[1]]


def test_usb_unplug_skips_warm_open_without_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.dra_radio as dra_radio_mod

    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return []

    monkeypatch.setattr(dra_radio_mod, "ensure_hid_module", _fake_hid_module)
    monkeypatch.setattr(dra_radio_mod, "list_cm119_hid_entries", fake_entries)
    radio.warm_open_hid()
    assert radio.usb_hid_present is False
    radio.warm_open_hid()
    assert radio.usb_hid_present is False


def test_usb_replug_after_unplug_restores_presence(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"old", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    state: dict[str, list[HidDeviceEntry]] = {"entries": []}

    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return list(state["entries"])

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", _fake_hid_module)
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", fake_entries)
    assert radio.note_usb_hid_presence() is False
    state["entries"] = [{"path": b"new", "vendor_id": 0, "product_id": 0}]
    with caplog.at_level("INFO"):
        assert radio.note_usb_hid_presence() is True
    assert any("connected again" in r.message for r in caplog.records)
    assert radio.sync_hid_adapters_if_replugged() is True
    assert radio.usb_hid_present is True
    assert radio.hid_adapter_path() == b"new"


def test_usb_reconnect_same_hid_path_still_unmutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    alsa_watch.last_unmuted_signature = (1,)
    def fake_signature(limit: int) -> tuple[int, ...]:
        _ = limit
        return (1,)

    monkeypatch.setattr(
        "app.dra_alsa_unmute.cm119_alsa_card_signature", fake_signature
    )
    unmuted: list[list[int]] = []
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"same", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    state: dict[str, list[HidDeviceEntry]] = {"entries": []}

    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return list(state["entries"])

    def sync_audio(*, after_usb_event: bool = False) -> bool:
        _ = after_usb_event
        return False

    def unmute_enabled() -> bool:
        return True

    def fake_cards(_radio: object) -> list[int]:
        return [1]

    def unmute_cards(cards: list[int], level: str) -> bool:
        _ = level
        unmuted.append(cards)
        return True

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", _fake_hid_module)
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", fake_entries)
    monkeypatch.setattr(
        radio,
        "sync_audio_output_indices_if_changed",
        sync_audio,
    )
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", unmute_enabled)
    monkeypatch.setattr(
        "app.dra_alsa_mixer.cm119_alsa_cards_for_radio",
        fake_cards,
    )
    monkeypatch.setattr(
        "app.dra_alsa_mixer.try_unmute_cm119_cards",
        unmute_cards,
    )
    assert radio.note_usb_hid_presence() is False
    state["entries"] = [{"path": b"same", "vendor_id": 0, "product_id": 0}]
    apply_cm119_replug_recovery(radio)
    assert unmuted == [[1]]
    assert radio.usb_hid_reconnected_pending is False


def test_stale_hid_path_env_falls_back_to_live_cm119(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.dra_radio as dra_radio_mod

    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"stale-pin", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    monkeypatch.setattr(dra_radio_mod, "DRA_HID_PATH_BYTES", b"deadbeef")
    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [cast(HidDeviceEntry, cast(object, {"path": b"live-path"}))]

    monkeypatch.setattr(dra_radio_mod, "ensure_hid_module", _fake_hid_module)
    monkeypatch.setattr(
        dra_radio_mod,
        "list_cm119_hid_entries",
        fake_entries,
    )
    entries = radio.enumerate_hid_entries()
    assert entries == [{"path": b"live-path"}]
    assert radio.sync_hid_adapters_if_replugged() is True
    assert radio.hid_adapter_path() == b"live-path"
