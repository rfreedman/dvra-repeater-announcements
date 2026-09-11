from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

MAX_PAUSE_SECONDS = 10.0
_PAUSE_TAG = re.compile(r"\[pause:\s*(\d+(?:\.\d+)?)\s*s?\]", re.IGNORECASE)


@dataclass(frozen=True)
class PcmChunk:
    pcm_int16: bytes
    sample_rate: int
    sample_width: int = 2
    channels: int = 1


@dataclass(frozen=True)
class SpeechSegment:
    text: str


@dataclass(frozen=True)
class PauseSegment:
    seconds: float


ScriptSegment = SpeechSegment | PauseSegment


def parse_script(text: str) -> list[ScriptSegment]:
    """Split script text on [pause:SECONDS] tags. Invalid tags stay in speech."""
    segments: list[ScriptSegment] = []
    last = 0
    for match in _PAUSE_TAG.finditer(text):
        speech = text[last : match.start()]
        if speech.strip():
            segments.append(SpeechSegment(speech))
        seconds = min(float(match.group(1)), MAX_PAUSE_SECONDS)
        segments.append(PauseSegment(seconds))
        last = match.end()
    tail = text[last:]
    if tail.strip():
        segments.append(SpeechSegment(tail))
    return segments


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


def materialize_chunks(chunks: Iterable[PcmChunk]) -> list[PcmChunk]:
    """Drain a PCM generator so speech and tagged silence exist before playback."""
    return list(chunks)
