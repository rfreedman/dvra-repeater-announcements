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


def test_apply_replug_recovery_hid_only_skips_amixer(monkeypatch):
    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    alsa_watch.last_unmuted_signature = (1,)
    monkeypatch.setattr("app.dra_alsa_mixer.cm119_alsa_card_signature", lambda limit: (1,))
    unmute_called = False
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

    def boom(*args, **kwargs):
        nonlocal unmute_called
        unmute_called = True
        return True

    monkeypatch.setattr("app.dra_alsa_mixer.try_unmute_cm119_cards", boom)
    apply_cm119_replug_recovery(radio)
    assert radio._hid_adapters[0].path == b"new"
    assert unmute_called is False
