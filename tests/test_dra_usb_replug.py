"""USB replug: refresh CM119 HID paths when ALSA card number is unchanged."""

from __future__ import annotations

from app.cm119 import Cm119HidAdapter
from app.dra_alsa_mixer import apply_cm119_replug_recovery, alsa_watch
from app.dra_radio import DraRadio


def test_sync_hid_adapters_updates_paths_and_clears_idle_credit(monkeypatch):
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"old-path", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    with radio._lock:
        radio._idle_since_mono = 100.0
        radio._last_cos_busy = False

    monkeypatch.setattr(
        radio,
        "_enumerate_hid_entries",
        lambda: [{"path": b"new-path"}],
    )
    assert radio.sync_hid_adapters_if_replugged() is True
    assert radio._hid_adapters[0].path == b"new-path"
    assert radio.continuous_idle_seconds() == 0.0
    assert radio.last_cos_busy is None


def test_apply_replug_recovery_hid_only_unmutes_speaker(monkeypatch):
    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    alsa_watch.last_unmuted_signature = (1,)
    monkeypatch.setattr("app.dra_alsa_mixer.cm119_alsa_card_signature", lambda limit: (1,))
    unmuted: list[list[int]] = []
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"old", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    monkeypatch.setattr(
        radio,
        "_enumerate_hid_entries",
        lambda: [{"path": b"new"}],
    )
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", lambda: True)
    monkeypatch.setattr(
        "app.dra_alsa_mixer.cm119_alsa_cards_for_radio",
        lambda _radio: [1],
    )
    monkeypatch.setattr(
        "app.dra_alsa_mixer.try_unmute_cm119_cards",
        lambda cards, level: unmuted.append(cards) or True,
    )
    apply_cm119_replug_recovery(radio)
    assert radio._hid_adapters[0].path == b"new"
    assert unmuted == [[1]]


def test_stale_hid_path_env_falls_back_to_live_cm119(monkeypatch):
    import app.dra_radio as dra_radio_mod

    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"stale-pin", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    monkeypatch.setattr(dra_radio_mod, "DRA_HID_PATH_BYTES", b"deadbeef")
    monkeypatch.setattr(dra_radio_mod, "ensure_hid_module", lambda: object())
    monkeypatch.setattr(
        dra_radio_mod,
        "list_cm119_hid_entries",
        lambda _hid: [{"path": b"live-path"}],
    )
    entries = radio._enumerate_hid_entries()
    assert entries == [{"path": b"live-path"}]
    assert radio.sync_hid_adapters_if_replugged() is True
    assert radio._hid_adapters[0].path == b"live-path"
