from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class PcmChunk:
    pcm_int16: bytes
    sample_rate: int
    sample_width: int = 2
    channels: int = 1


def split_sentences(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    parts = [part.strip() for part in _SENTENCE_RE.split(stripped) if part.strip()]
    return parts or [stripped]


def float_to_pcm16(audio: np.ndarray) -> bytes:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    samples = np.clip(samples, -1.0, 1.0)
    return (samples * 32767.0).astype(np.int16).tobytes()


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
