from __future__ import annotations

import sounddevice as sd

from app.audio import PcmChunk, silence_chunk
from app.playback import play_chunks


def test_play_chunks_drains_generator_before_opening_stream(monkeypatch):
    events: list[str] = []

    def generate():
        events.append("speech1")
        yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)
        events.append("pause")
        yield silence_chunk(0.5, 22050)
        events.append("speech2")
        yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)

    class FakeStream:
        def __init__(self, **kwargs):
            events.append("stream_open")

        def start(self):
            events.append("stream_start")

        def write(self, samples):
            events.append("write")

        def stop(self):
            events.append("stream_stop")

        def close(self):
            events.append("stream_close")

    monkeypatch.setattr(sd, "OutputStream", FakeStream)
    play_chunks(generate())
    assert events[:5] == ["speech1", "pause", "speech2", "stream_open", "stream_start"]
    assert events.count("write") == 3
