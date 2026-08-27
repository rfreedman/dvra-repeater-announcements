from __future__ import annotations

import re
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
