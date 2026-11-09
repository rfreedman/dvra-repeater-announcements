from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import numpy as np

from app.audio import PcmChunk, silence_chunk
from app.playback import play_chunks


import pytest
def test_play_chunks_drains_generator_before_opening_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    def generate() -> Iterator[PcmChunk]:
        events.append("speech1")
        yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)
        events.append("pause")
        yield silence_chunk(0.5, 22050)
        events.append("speech2")
        yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)

    class FakeStream:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs
            events.append("stream_open")

        def start(self) -> None:
            events.append("stream_start")

        def write(self, samples: np.ndarray) -> None:
            _ = samples
            events.append("write")

        def stop(self) -> None:
            events.append("stream_stop")

        def close(self) -> None:
            events.append("stream_close")

    fake_sd = types.ModuleType("sounddevice")
    setattr(fake_sd, "OutputStream", FakeStream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    play_chunks(generate())
    assert events[:5] == ["speech1", "pause", "speech2", "stream_open", "stream_start"]
    assert events.count("write") == 3
