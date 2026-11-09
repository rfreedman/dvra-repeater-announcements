"""DRA USB sample rate: ALSA hw vs PortAudio default."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence


import pytest
def test_dra_device_samplerate_prefers_alsa_hw_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.dra_portaudio.sys.platform", "linux")

    def fake_query(index: int) -> dict[str, object]:
        _ = index
        return {"name": "USB PnP Sound Device: Audio (hw:1,0)", "default_samplerate": 44100.0}

    monkeypatch.setattr("sounddevice.query_devices", fake_query)
    def fake_card(_index: int) -> int:
        return 1

    monkeypatch.setattr(
        "app.dra_alsa_unmute.alsa_card_from_portaudio_device", fake_card
    )

    def fake_arecord(
        cmd: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        assert "-D" in cmd and "hw:1,0" in cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="RATE: 48000 (48000-48000)\n", stderr=""
        )

    monkeypatch.setattr("app.dra_portaudio.subprocess.run", fake_arecord)
    from app.dra_portaudio import dra_device_samplerate

    assert dra_device_samplerate(3) == 48000
