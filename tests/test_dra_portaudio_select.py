"""PortAudio DRA device selection after USB replug."""

from __future__ import annotations

from app.dra_portaudio import DraOutputDevice, prefer_cm119_hw_devices


import pytest
def test_prefer_cm119_hw_devices_filters_non_cm119(monkeypatch: pytest.MonkeyPatch) -> None:
    hw: list[DraOutputDevice] = [
        (
            3,
            {
                "name": "HDA Intel PCH: HDMI 2 (hw:0,8)",
                "max_output_channels": 2,
            },
        ),
        (
            4,
            {
                "name": "USB PnP Sound Device: Audio (hw:1,0)",
                "max_output_channels": 2,
            },
        ),
    ]
    def fake_signature(limit: int) -> tuple[int, ...]:
        _ = limit
        return (1,)

    def fake_card(index: int) -> int | None:
        return {3: 0, 4: 1}.get(index)

    monkeypatch.setattr(
        "app.dra_alsa_unmute.cm119_alsa_card_signature", fake_signature
    )
    monkeypatch.setattr(
        "app.dra_alsa_unmute.alsa_card_from_portaudio_device", fake_card
    )
    picked = prefer_cm119_hw_devices(hw)
    assert picked == [(4, hw[1][1])]
