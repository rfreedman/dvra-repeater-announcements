from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class PcmChunk:
    pcm_int16: bytes
    sample_rate: int
    sample_width: int = 2
    channels: int = 1


def silence_chunk(
    seconds: float,
    sample_rate: int,
    sample_width: int = 2,
    channels: int = 1,
) -> PcmChunk:
    frames = int(max(0.0, seconds) * sample_rate)
    return PcmChunk(
        pcm_int16=bytes(frames * channels * sample_width),
        sample_rate=sample_rate,
        sample_width=sample_width,
        channels=channels,
    )


def with_sentence_pauses(chunks: Iterator[PcmChunk], seconds: float) -> Iterator[PcmChunk]:
    if seconds <= 0:
        yield from chunks
        return
    pending: PcmChunk | None = None
    for chunk in chunks:
        if pending is not None:
            yield pending
            yield silence_chunk(
                seconds,
                pending.sample_rate,
                pending.sample_width,
                pending.channels,
            )
        pending = chunk
    if pending is not None:
        yield pending
