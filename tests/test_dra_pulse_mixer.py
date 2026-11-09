"""PipeWire/Pulse sink unmute before DRA USB playback."""

from __future__ import annotations

import subprocess

import pytest
def test_pulse_sink_unmute_calls_pactl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.dra_pulse_mixer.sys.platform", "linux")
    calls: list[list[str]] = []

    def fake_pactl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(["pactl", *args], 0)

    monkeypatch.setattr("app.dra_pulse_mixer._pactl", fake_pactl)
    from app.dra_pulse_mixer import sync_pulse_sink_unmute_for_portaudio_name

    name = "alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-stereo"
    sync_pulse_sink_unmute_for_portaudio_name(name)
    assert calls == [
        ["set-sink-mute", name, "0"],
        ["set-sink-volume", name, "100%"],
    ]
