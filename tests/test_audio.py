from __future__ import annotations

from app.audio import (
    PauseSegment,
    PcmChunk,
    SpeechSegment,
    materialize_chunks,
    parse_script,
    silence_chunk,
)


def test_parse_script_splits_pause_tags():
    segments = parse_script("Hello [pause:0.5] world [pause:0.1s] end")
    assert [type(item) for item in segments] == [
        SpeechSegment,
        PauseSegment,
        SpeechSegment,
        PauseSegment,
        SpeechSegment,
    ]
    assert segments[1].seconds == 0.5
    assert segments[3].seconds == 0.1


def test_silence_chunk_duration_matches_sample_rate():
    chunk = silence_chunk(0.5, sample_rate=22050)
    assert len(chunk.pcm_int16) == int(0.5 * 22050) * 2
    assert chunk.sample_rate == 22050


def test_materialize_chunks_includes_silence_before_later_speech():
    events: list[str] = []

    def generate():
        events.append("speech1")
        yield PcmChunk(pcm_int16=b"\x01\x00", sample_rate=22050)
        events.append("pause")
        yield silence_chunk(0.5, 22050)
        events.append("speech2")
        yield PcmChunk(pcm_int16=b"\x02\x00", sample_rate=22050)

    buffered = materialize_chunks(generate())
    assert events == ["speech1", "pause", "speech2"]
    assert len(buffered) == 3
    assert buffered[1].pcm_int16 == silence_chunk(0.5, 22050).pcm_int16
