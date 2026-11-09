"""Playback unmutes CM119 Speaker immediately before opening PortAudio streams."""

from __future__ import annotations

import numpy as np

from app.audio import PcmChunk


import pytest
def test_playback_unmutes_by_portaudio_indices_before_stream(monkeypatch: pytest.MonkeyPatch, stub_dra_playback_dependencies: None) -> None:
    _ = stub_dra_playback_dependencies
    unmute_calls: list[list[int]] = []

    def fake_unmute(indices: list[int]) -> None:
        unmute_calls.append(list(indices))

    def fake_device_name(index: int) -> str:
        return f"alsa_output.usb-test-{index}"

    def fake_pulse_unmute(_name: str) -> None:
        pass

    monkeypatch.setattr(
        "app.dra_alsa_unmute.sync_cm119_speaker_unmute_for_indices",
        fake_unmute,
    )
    monkeypatch.setattr(
        "app.dra_playback.portaudio_output_device_name",
        fake_device_name,
    )
    monkeypatch.setattr(
        "app.dra_pulse_mixer.sync_pulse_sink_unmute_for_portaudio_name",
        fake_pulse_unmute,
    )

    class FakeStream:
        samplerate: int = 44100

        def __enter__(self) -> FakeStream:
            return self

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: object | None,
        ) -> None:
            pass

        def write_mono_f32(self, mono: np.ndarray) -> None:
            _ = mono
            pass

    def fake_stream(*args: object, **kwargs: object) -> FakeStream:
        _ = args
        _ = kwargs
        return FakeStream()

    monkeypatch.setattr("app.dra_playback.DraUsbPlayback", fake_stream)

    from app.dra_playback import play_pcm_chunks_to_dra_devices

    play_pcm_chunks_to_dra_devices(
        [PcmChunk(pcm_int16=b"\x00\x10" * 1000, sample_rate=22050)],
        device_indices=[4],
        channel="left",
    )

    assert unmute_calls == [[4]]
