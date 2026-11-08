"""Playback unmutes CM119 Speaker immediately before opening PortAudio streams."""

from __future__ import annotations

import numpy as np

from app.audio import PcmChunk


def test_playback_unmutes_by_portaudio_indices_before_stream(
    monkeypatch, stub_dra_playback_dependencies
):
    unmute_calls: list[list[int]] = []

    monkeypatch.setattr(
        "app.dra_alsa_unmute.sync_cm119_speaker_unmute_for_indices",
        lambda indices: unmute_calls.append(list(indices)),
    )
    monkeypatch.setattr(
        "app.dra_playback.portaudio_output_device_name",
        lambda index: f"alsa_output.usb-test-{index}",
    )
    monkeypatch.setattr(
        "app.dra_pulse_mixer.sync_pulse_sink_unmute_for_portaudio_name",
        lambda _name: None,
    )

    class FakeStream:
        samplerate = 44100

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            pass

        def write_mono_f32(self, mono: np.ndarray) -> None:
            pass

    monkeypatch.setattr("app.dra_playback.DraUsbPlayback", lambda *args, **kwargs: FakeStream())

    from app.dra_playback import play_pcm_chunks_to_dra_devices

    play_pcm_chunks_to_dra_devices(
        [PcmChunk(pcm_int16=b"\x00\x10" * 1000, sample_rate=22050)],
        device_indices=[4],
        channel="left",
    )

    assert unmute_calls == [[4]]
