"""Decode a story MP3 to the PCM chunks the radio already plays."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.audio import PcmChunk

SAMPLE_RATE = 22050


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def mp3_to_chunks(path: Path, *, sample_rate: int = SAMPLE_RATE) -> list[PcmChunk]:
    if not ffmpeg_available():
        raise OSError("ffmpeg and ffprobe are required to play Newsline audio")
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-",
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(detail or f"ffmpeg failed on {path.name}")
    pcm = completed.stdout
    frame_bytes = sample_rate  # about half a second of mono s16
    chunks: list[PcmChunk] = []
    for start in range(0, len(pcm), frame_bytes):
        block = pcm[start : start + frame_bytes]
        if block:
            chunks.append(PcmChunk(pcm_int16=block, sample_rate=sample_rate))
    if not chunks:
        chunks.append(PcmChunk(pcm_int16=b"", sample_rate=sample_rate))
    return chunks
