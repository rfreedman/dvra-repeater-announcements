"""Linux CM119 playback via aplay (bypasses silent PortAudio on some NUCs)."""

from __future__ import annotations

import numpy as np


def test_play_mono_f32_uses_aplay_on_linux(monkeypatch):
    monkeypatch.setattr("app.dra_playback.sys.platform", "linux")
    monkeypatch.setenv("TTS_DRA_LINUX_PLAYBACK", "aplay")
    aplay_calls: list[tuple[str, int]] = []

    monkeypatch.setattr(
        "app.dra_alsa_mixer.sync_cm119_speaker_unmute_for_indices",
        lambda _indices: None,
    )
    monkeypatch.setattr(
        "app.dra_playback._alsa_playback_devices_for_portaudio_index",
        lambda _index: ["hw:1,0", "plughw:1,0"],
    )

    def fake_first(
        pcm: bytes,
        *,
        sample_rate: int,
        alsa_devices: list[str],
        should_abort=None,
        register_proc=None,
    ) -> str:
        aplay_calls.append((alsa_devices[0], sample_rate))
        assert pcm
        assert sample_rate == 44100
        return alsa_devices[0]

    monkeypatch.setattr(
        "app.dra_playback._play_s16_stereo_via_aplay_first_working",
        fake_first,
    )

    from app.dra_playback import play_mono_f32_to_dra_device

    t = np.linspace(0, 0.1, 4410, endpoint=False)
    mono = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    play_mono_f32_to_dra_device(
        mono, device_index=3, channel="left", samplerate=44100
    )
    assert aplay_calls == [("hw:1,0", 44100)]
