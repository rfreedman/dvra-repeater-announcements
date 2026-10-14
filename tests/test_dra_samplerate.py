"""DRA USB sample rate: ALSA hw vs PortAudio default."""

from __future__ import annotations

import subprocess


def test_dra_device_samplerate_prefers_alsa_hw_rate(monkeypatch):
    monkeypatch.setattr("app.dra_playback.sys.platform", "linux")

    def fake_query(index: int) -> dict:
        return {"name": "USB PnP Sound Device: Audio (hw:1,0)", "default_samplerate": 44100.0}

    monkeypatch.setattr("sounddevice.query_devices", fake_query)
    monkeypatch.setattr("app.dra_alsa_mixer.alsa_card_from_portaudio_device", lambda _i: 1)

    def fake_arecord(cmd, **kwargs):
        assert "-D" in cmd and "hw:1,0" in cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="RATE: 48000 (48000-48000)\n", stderr=""
        )

    monkeypatch.setattr("app.dra_playback.subprocess.run", fake_arecord)
    from app.dra_playback import dra_device_samplerate

    assert dra_device_samplerate(3) == 48000
