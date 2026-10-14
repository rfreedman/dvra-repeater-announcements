"""PortAudio DRA device selection after USB replug."""

from __future__ import annotations

from app.dra_playback import _prefer_cm119_hw_devices


def test_prefer_cm119_hw_devices_filters_non_cm119(monkeypatch):
    hw = [
        (3, {"name": "HDA Intel PCH: HDMI 2 (hw:0,8)"}),
        (4, {"name": "USB PnP Sound Device: Audio (hw:1,0)"}),
    ]
    monkeypatch.setattr(
        "app.dra_alsa_mixer.cm119_alsa_card_signature",
        lambda limit: (1,),
    )
    monkeypatch.setattr(
        "app.dra_alsa_mixer.alsa_card_from_portaudio_device",
        lambda index: {3: 0, 4: 1}.get(index),
    )
    picked = _prefer_cm119_hw_devices(hw)
    assert picked == [(4, hw[1][1])]
