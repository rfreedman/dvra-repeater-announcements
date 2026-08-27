from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from app.audio import PcmChunk


def play_chunks(chunks: Iterable[PcmChunk]) -> None:
    """Play 16-bit PCM chunks from memory. No files are written."""
    try:
        import sounddevice as sd
    except OSError as exc:
        raise RuntimeError(
            "PortAudio is missing. On macOS: brew install portaudio. "
            "On Debian/Raspberry Pi: sudo apt install libportaudio2"
        ) from exc

    stream: sd.OutputStream | None = None
    try:
        for chunk in chunks:
            if not chunk.pcm_int16:
                continue
            if stream is None:
                stream = sd.OutputStream(
                    samplerate=chunk.sample_rate,
                    channels=chunk.channels,
                    dtype="int16",
                )
                stream.start()
            samples = np.frombuffer(chunk.pcm_int16, dtype=np.int16)
            if chunk.channels > 1:
                samples = samples.reshape(-1, chunk.channels)
            stream.write(samples)
    finally:
        if stream is not None:
            stream.stop()
            stream.close()
