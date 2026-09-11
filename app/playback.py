from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from app.audio import PcmChunk, materialize_chunks


def play_chunks(chunks: Iterable[PcmChunk]) -> None:
    """Play 16-bit PCM chunks from memory. No files are written."""
    buffered = [chunk for chunk in materialize_chunks(chunks) if chunk.pcm_int16]
    if not buffered:
        return
    try:
        import sounddevice as sd
    except OSError as exc:
        raise RuntimeError(
            "PortAudio is missing. On macOS: brew install portaudio. "
            "On Debian/Raspberry Pi: sudo apt install libportaudio2"
        ) from exc

    stream: sd.OutputStream | None = None
    try:
        first = buffered[0]
        stream = sd.OutputStream(
            samplerate=first.sample_rate,
            channels=first.channels,
            dtype="int16",
        )
        stream.start()
        for chunk in buffered:
            samples = np.frombuffer(chunk.pcm_int16, dtype=np.int16)
            if chunk.channels > 1:
                samples = samples.reshape(-1, chunk.channels)
            stream.write(samples)
    finally:
        if stream is not None:
            stream.stop()
            stream.close()
