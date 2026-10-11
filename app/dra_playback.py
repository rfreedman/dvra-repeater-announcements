"""PortAudio output to DRA-36M USB devices (COMM OK heartbeat + left/right routing)."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from app.audio import PcmChunk, materialize_chunks

_DRA_NAME_MARKERS = ("USB PnP Sound Device", "C-Media")


def is_dra_audio_name(name: str) -> bool:
    return any(marker in name for marker in _DRA_NAME_MARKERS)


def list_dra_output_devices() -> list[tuple[int, dict]]:
    import sounddevice as sd

    out: list[tuple[int, dict]] = []
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] < 1:
            continue
        if is_dra_audio_name(dev["name"]):
            out.append((index, dev))
    return out


def resolve_dra_audio_indices(
    *,
    device_specs: list[str] | None = None,
) -> list[int]:
    """Return PortAudio output indices for each DRA.

    *device_specs* — optional substrings or integer indices (one per DRA). When omitted,
    every DRA-like output device is used (sorted by index).
    """
    import sounddevice as sd

    all_dra = list_dra_output_devices()
    if not all_dra:
        raise RuntimeError(
            "No DRA USB audio device found (look for C-Media / USB PnP Sound Device in PortAudio)"
        )

    if not device_specs:
        return [index for index, _dev in all_dra]

    indices: list[int] = []
    for spec in device_specs:
        spec = spec.strip()
        if not spec:
            continue
        if spec.isdigit():
            idx = int(spec)
            dev = sd.query_devices(idx)
            if dev["max_output_channels"] < 1:
                raise RuntimeError(f"TTS_DRA_AUDIO_DEVICES index {idx} has no output channels")
            indices.append(idx)
            continue
        matches = [(i, d) for i, d in all_dra if spec.lower() in d["name"].lower()]
        if len(matches) != 1:
            names = [d["name"] for _i, d in all_dra]
            raise RuntimeError(
                f"TTS_DRA_AUDIO_DEVICES entry {spec!r} matched {len(matches)} devices "
                f"(expected 1). Available: {names}"
            )
        indices.append(matches[0][0])
    if not indices:
        raise RuntimeError("TTS_DRA_AUDIO_DEVICES was set but parsed empty")
    return indices


def mono_to_channel(mono: np.ndarray, channel: str) -> np.ndarray:
    if channel == "right":
        return np.column_stack([np.zeros_like(mono), mono])
    if channel == "left":
        return np.column_stack([mono, np.zeros_like(mono)])
    return mono


def _output_channels(channel: str) -> int:
    return 1 if channel == "mono" else 2


def dra_device_samplerate(device_index: int) -> int:
    """PortAudio default rate for a DRA USB device (CM119 is typically 44100 Hz)."""
    import sounddevice as sd

    return int(sd.query_devices(device_index)["default_samplerate"])


def resample_pcm_int16_mono(pcm_int16: bytes, *, from_rate: int, to_rate: int) -> bytes:
    """Linear resample mono int16 PCM (Piper 22050 Hz → DRA 44100 Hz on Linux)."""
    if from_rate == to_rate or not pcm_int16:
        return pcm_int16
    samples = np.frombuffer(pcm_int16, dtype=np.int16)
    if samples.size == 0:
        return pcm_int16
    out_count = max(1, int(round(samples.size * to_rate / from_rate)))
    x_old = np.arange(samples.size, dtype=np.float64)
    x_new = np.linspace(0, samples.size - 1, out_count)
    resampled = np.interp(x_new, x_old, samples.astype(np.float64)).astype(np.int16)
    return resampled.tobytes()


def _write_mono_to_stream(stream, mono_f32: np.ndarray, *, channel: str) -> None:
    out = mono_to_channel(mono_f32, channel)
    if out.ndim == 1:
        stream.write(out.reshape(-1, 1))
    else:
        stream.write(out)


class DraUsbPlayback:
    """One PortAudio output stream to a DRA (USB audio heartbeat)."""

    def __init__(self, device_index: int, channel: str, samplerate: int):
        import sounddevice as sd

        self.device_index = device_index
        self.channel = channel
        self.samplerate = samplerate
        self._channels = _output_channels(channel)
        self._stream: sd.OutputStream | None = None

    def __enter__(self):
        import sounddevice as sd

        self._stream = sd.OutputStream(
            device=self.device_index,
            samplerate=self.samplerate,
            channels=self._channels,
            dtype="float32",
        )
        self._stream.start()
        return self

    def __exit__(self, *_exc) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def write_silence(self, seconds: float) -> None:
        if seconds <= 0 or self._stream is None:
            return
        frames = int(seconds * self.samplerate)
        if frames <= 0:
            return
        zeros = np.zeros(frames, dtype=np.float32)
        _write_mono_to_stream(self._stream, zeros, channel=self.channel)

    def write_pcm_int16_mono(self, pcm_int16: bytes, *, sample_rate: int) -> None:
        if self._stream is None or not pcm_int16:
            return
        if sample_rate != self.samplerate:
            pcm_int16 = resample_pcm_int16_mono(
                pcm_int16, from_rate=sample_rate, to_rate=self.samplerate
            )
        mono = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
        _write_mono_to_stream(self._stream, mono, channel=self.channel)


def play_pcm_chunks_to_single_dra(
    chunks: Iterable[PcmChunk],
    *,
    device_index: int,
    channel: str = "left",
    lead_silence: float = 0.0,
) -> None:
    """Play Piper chunks on one DRA USB output at the device's native sample rate."""
    buffered = [chunk for chunk in materialize_chunks(chunks) if chunk.pcm_int16]
    if not buffered:
        return
    stream_rate = dra_device_samplerate(device_index)
    with DraUsbPlayback(device_index, channel, stream_rate) as playback:
        playback.write_silence(lead_silence)
        for chunk in buffered:
            playback.write_pcm_int16_mono(chunk.pcm_int16, sample_rate=chunk.sample_rate)


def play_pcm_chunks_to_dra_devices(
    chunks: Iterable[PcmChunk],
    *,
    device_indices: list[int],
    channel: str = "left",
    lead_silence: float = 0.0,
) -> None:
    """Play the same announcement on one or two DRA USB outputs (production dual-TX)."""
    buffered = [chunk for chunk in materialize_chunks(chunks) if chunk.pcm_int16]
    if not buffered:
        return

    streams: list[DraUsbPlayback] = []
    try:
        for index in device_indices:
            stream_rate = dra_device_samplerate(index)
            playback = DraUsbPlayback(index, channel, stream_rate)
            playback.__enter__()
            streams.append(playback)
        for playback in streams:
            playback.write_silence(lead_silence)
        for chunk in buffered:
            for playback in streams:
                playback.write_pcm_int16_mono(
                    chunk.pcm_int16,
                    sample_rate=chunk.sample_rate,
                )
    finally:
        for playback in streams:
            playback.__exit__()
