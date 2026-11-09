"""PortAudio output to DRA-36M USB devices (COMM OK heartbeat + left/right routing)."""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from types import TracebackType
from typing import Protocol, Self, cast

import numpy as np

log = logging.getLogger("app.dra_playback")

from app.audio import PcmChunk, materialize_chunks
from app.dra_portaudio import (
    dra_device_samplerate,
    portaudio_lock,
    portaudio_output_device_name,
)


class _PortAudioWriteStream(Protocol):
    def write(self, data: object) -> None: ...


def mono_to_channel(mono: np.ndarray, channel: str) -> np.ndarray:
    if channel == "right":
        return np.column_stack([np.zeros_like(mono), mono])
    if channel == "left":
        return np.column_stack([mono, np.zeros_like(mono)])
    return mono


def _output_channels(channel: str) -> int:
    return 1 if channel == "mono" else 2


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


# PortAudio ``write`` blocks until the data is buffered, so with two radios each
# stream gets a short block in turn; writing a whole announcement to one stream
# would make the second radio wait (keyed, silent) until the first finished.
WRITE_BLOCK_SECONDS = 0.05


def _write_mono_to_stream(
    stream: _PortAudioWriteStream,
    mono_f32: np.ndarray,
    *,
    channel: str,
    stream_dtype: str = "float32",
) -> None:
    out = mono_to_channel(mono_f32, channel)
    if np.dtype(stream_dtype).name == "int16":
        if out.ndim == 1:
            out = (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).reshape(-1, 1)
        else:
            out = (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16)
    if out.ndim == 1:
        stream.write(out.reshape(-1, 1))
    else:
        stream.write(out)


def _alsa_playback_devices_for_portaudio_index(device_index: int) -> list[str]:
    from app.config import DRA_ALSA_PLAYBACK_DEVICE
    from app.dra_alsa_unmute import alsa_card_from_portaudio_device

    if DRA_ALSA_PLAYBACK_DEVICE:
        return [DRA_ALSA_PLAYBACK_DEVICE]
    card = alsa_card_from_portaudio_device(device_index)
    if card is None:
        raise RuntimeError(
            f"Could not map PortAudio index {device_index} to ALSA hw "
            + "(expected device name with (hw:N,0))"
        )
    # Prefer direct hw for CM119 USB; plughw can route through PipeWire and stay silent.
    return [f"hw:{card},0", f"plughw:{card},0"]


def _mono_f32_to_interleaved_s16(mono_f32: np.ndarray, channel: str) -> bytes:
    stereo = mono_to_channel(mono_f32.astype(np.float32), channel)
    if stereo.ndim == 1:
        stereo = stereo.reshape(-1, 1)
    return (np.clip(stereo, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


_APLAY_ABORT_POLL_SECONDS = 0.05


def play_s16_stereo_via_aplay(
    pcm: bytes,
    *,
    sample_rate: int,
    alsa_device: str,
    should_abort: Callable[[], bool] | None = None,
    register_proc: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> None:
    from app.radio_errors import TransmitAbortError

    if not pcm:
        return
    if should_abort is not None and should_abort():
        raise TransmitAbortError("DRA USB playback aborted")

    cmd = [
        "aplay",
        "-q",
        "-D",
        alsa_device,
        "-t",
        "raw",
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        "2",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "aplay not found; install alsa-utils on the NUC (docs/NUC-notes.md)"
        ) from exc

    if register_proc is not None:
        register_proc(proc)
    try:
        assert proc.stdin is not None
        try:
            _ = proc.stdin.write(pcm)
            proc.stdin.close()
        except BrokenPipeError as exc:
            proc.stdin = None
            try:
                proc.kill()
                _ = proc.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
            if register_proc is not None or (
                should_abort is not None and should_abort()
            ):
                raise TransmitAbortError("DRA USB playback aborted") from exc
            raise RuntimeError(f"aplay to {alsa_device} failed: broken pipe") from exc
        proc.stdin = None
        while proc.poll() is None:
            if should_abort is not None and should_abort():
                proc.kill()
                try:
                    _ = proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _ = proc.wait()
                raise TransmitAbortError("DRA USB playback aborted")
            time.sleep(_APLAY_ABORT_POLL_SECONDS)
        stderr = (proc.stderr.read() if proc.stderr is not None else b"").decode(
            "utf-8", errors="replace"
        ).strip()
        if proc.returncode != 0:
            if should_abort is not None and should_abort():
                raise TransmitAbortError("DRA USB playback aborted")
            raise RuntimeError(f"aplay to {alsa_device} failed: {stderr}")
        if stderr:
            log.debug("aplay stderr for %s: %s", alsa_device, stderr)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


def _play_s16_stereo_via_aplay_first_working(
    pcm: bytes,
    *,
    sample_rate: int,
    alsa_devices: list[str],
    should_abort: Callable[[], bool] | None = None,
    register_proc: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> str:
    from app.radio_errors import TransmitAbortError

    errors: list[str] = []
    for alsa_device in alsa_devices:
        try:
            play_s16_stereo_via_aplay(
                pcm,
                sample_rate=sample_rate,
                alsa_device=alsa_device,
                should_abort=should_abort,
                register_proc=register_proc,
            )
        except TransmitAbortError:
            raise
        except RuntimeError as exc:
            errors.append(f"{alsa_device}: {exc}")
            continue
        return alsa_device
    if should_abort is not None and should_abort():
        raise TransmitAbortError("DRA USB playback aborted")
    raise RuntimeError(
        "aplay failed on all ALSA devices: " + "; ".join(errors)
    )


def _linux_use_aplay() -> bool:
    from app.config import dra_linux_playback_backend

    return sys.platform.startswith("linux") and dra_linux_playback_backend() == "aplay"


def play_dra_lead_silence(
    *,
    device_indices: list[int],
    channel: str,
    lead_seconds: float,
    should_abort: Callable[[], bool] | None = None,
    register_aplay_proc: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> None:
    """Stream USB silence after PTT so COMM OK / heartbeat is up before speech.

    With two DRAs, both streams must start together. Sequential lead lets the first
    radio's COMM OK drop before the second starts, so RF unkeys/rekeys before speech.
    """
    if lead_seconds <= 0 or not device_indices:
        return

    def _play_one(index: int) -> None:
        sr = dra_device_samplerate(index)
        frames = int(round(max(0.0, lead_seconds) * sr))
        if frames <= 0:
            return
        silence = np.zeros(frames, dtype=np.float32)
        play_mono_f32_to_dra_device(
            silence,
            device_index=index,
            channel=channel,
            samplerate=sr,
            should_abort=should_abort,
            register_aplay_proc=register_aplay_proc,
        )

    if len(device_indices) == 1:
        _play_one(device_indices[0])
        return

    errors: list[BaseException] = []
    threads: list[threading.Thread] = []

    def _target(index: int) -> None:
        try:
            _play_one(index)
        except BaseException as exc:  # noqa: BLE001 — surface first failure after join
            errors.append(exc)

    for index in device_indices:
        thread = threading.Thread(
            target=_target,
            args=(index,),
            name=f"dra-lead-{index}",
        )
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


def play_mono_f32_to_dra_device(
    mono_f32: np.ndarray,
    *,
    device_index: int,
    channel: str,
    samplerate: int,
    should_abort: Callable[[], bool] | None = None,
    register_aplay_proc: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> None:
    """Play one float32 mono program on a DRA (bench + shared Linux aplay path)."""
    if mono_f32.size == 0:
        return
    if _linux_use_aplay():
        from app.dra_alsa_unmute import sync_cm119_speaker_unmute_for_indices

        sync_cm119_speaker_unmute_for_indices([device_index])
        alsa_candidates = _alsa_playback_devices_for_portaudio_index(device_index)
        peak_f32 = float(cast(float | int, cast(object, np.max(np.abs(mono_f32)))))
        pcm = _mono_f32_to_interleaved_s16(mono_f32, channel)
        s16 = np.frombuffer(pcm, dtype=np.int16)
        peak_s16 = (
            int(cast(float | int, cast(object, np.max(np.abs(s16))))) if s16.size else 0
        )
        used = _play_s16_stereo_via_aplay_first_working(
            pcm,
            sample_rate=samplerate,
            alsa_devices=alsa_candidates,
            should_abort=should_abort,
            register_proc=register_aplay_proc,
        )
        line = (
            f"DRA aplay → {used} ({samplerate} Hz, channel={channel}, "
            f"peak_f32={peak_f32:.4f}, peak_s16={peak_s16}, "
            f"bytes={len(pcm)}, PortAudio[{device_index}])"
        )
        log.info(line)
        print(line, flush=True)
        return
    print(
        f"DRA PortAudio playback (PortAudio[{device_index}], {samplerate} Hz, channel={channel})",
        flush=True,
    )
    with DraUsbPlayback(device_index, channel, samplerate) as playback:
        playback.write_mono_f32(mono_f32)


def mono_program_f32(chunks: Iterable[PcmChunk], *, samplerate: int, lead_silence: float) -> np.ndarray:
    """Lead silence + every chunk, resampled to ``samplerate``, as float32 mono."""
    parts = [np.zeros(int(max(0.0, lead_silence) * samplerate), dtype=np.int16)]
    for chunk in chunks:
        if not chunk.pcm_int16:
            continue
        pcm = resample_pcm_int16_mono(chunk.pcm_int16, from_rate=chunk.sample_rate, to_rate=samplerate)
        parts.append(np.frombuffer(pcm, dtype=np.int16))
    return np.concatenate(parts).astype(np.float32) / 32768.0


class DraUsbPlayback:
    """One PortAudio output stream to a DRA (USB audio heartbeat)."""

    device_index: int
    channel: str
    samplerate: int
    _channels: int
    _dtype: str
    _stream: object | None

    def __init__(self, device_index: int, channel: str, samplerate: int) -> None:
        self.device_index = device_index
        self.channel = channel
        self.samplerate = samplerate
        self._channels = _output_channels(channel)
        self._dtype = "float32"
        self._stream = None

    def __enter__(self) -> Self:
        import sounddevice as sd

        stream = sd.OutputStream(
            device=self.device_index,
            samplerate=self.samplerate,
            channels=self._channels,
            dtype=self._dtype,
        )
        self._stream = stream
        cast(Callable[[], None], getattr(stream, "start"))()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        _ = exc_type, exc, tb
        if self._stream is not None:
            stop = cast(Callable[[], None], getattr(self._stream, "stop"))
            close = cast(Callable[[], None], getattr(self._stream, "close"))
            stop()
            close()
            self._stream = None

    def write_mono_f32(self, mono: np.ndarray) -> None:
        if self._stream is None or mono.size == 0:
            return
        stream = cast(_PortAudioWriteStream, self._stream)
        block = max(1, int(round(WRITE_BLOCK_SECONDS * self.samplerate)))
        for start in range(0, mono.size, block):
            _write_mono_to_stream(
                stream,
                mono[start : start + block],
                channel=self.channel,
                stream_dtype=self._dtype,
            )


def write_programs_interleaved(
    streams: list[DraUsbPlayback],
    programs: list[np.ndarray],
    *,
    block_seconds: float = WRITE_BLOCK_SECONDS,
    should_abort: Callable[[], bool] | None = None,
) -> None:
    """Feed each stream its program in lock-step blocks so all radios play together."""
    from app.radio_errors import TransmitAbortError

    if not streams:
        return
    duration = max(len(program) / stream.samplerate for stream, program in zip(streams, programs))
    blocks = int(cast(float | int, cast(object, np.ceil(duration / block_seconds))))
    for block in range(blocks):
        if should_abort is not None and should_abort():
            raise TransmitAbortError("DRA USB playback aborted")
        start_s = block * block_seconds
        end_s = start_s + block_seconds
        for stream, program in zip(streams, programs):
            start = int(round(start_s * stream.samplerate))
            end = int(round(end_s * stream.samplerate))
            if start < len(program):
                stream.write_mono_f32(program[start:end])


def play_pcm_chunks_to_dra_devices(
    chunks: Iterable[PcmChunk],
    *,
    device_indices: list[int],
    channel: str = "left",
    lead_silence: float = 0.0,
    should_abort: Callable[[], bool] | None = None,
    register_stream: Callable[[DraUsbPlayback], None] | None = None,
    register_aplay_proc: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> None:
    """Play the same announcement on one or two DRA USB outputs at the same time."""
    buffered = [chunk for chunk in materialize_chunks(chunks) if chunk.pcm_int16]
    if not buffered:
        log.warning("DRA playback skipped: no PCM samples in chunks")
        return

    with portaudio_lock:
        from app.dra_alsa_unmute import sync_cm119_speaker_unmute_for_indices
        from app.dra_pulse_mixer import sync_pulse_sink_unmute_for_portaudio_name

        sync_cm119_speaker_unmute_for_indices(device_indices)
        for index in device_indices:
            sync_pulse_sink_unmute_for_portaudio_name(portaudio_output_device_name(index))

        from app.radio_errors import TransmitAbortError

        try:
            samplerates = [dra_device_samplerate(index) for index in device_indices]
        except Exception as exc:
            if should_abort is not None and should_abort():
                raise TransmitAbortError("DRA USB playback aborted") from exc
            raise
        programs = [
            mono_program_f32(buffered, samplerate=sr, lead_silence=lead_silence)
            for sr in samplerates
        ]
        peak_values = [
            float(cast(float | int, cast(object, np.max(np.abs(program)))))
            for program in programs
            if program.size
        ]
        peak = max(peak_values)
        frames = max(len(program) for program in programs)
        log.info(
            "DRA PCM program: %s frames, peak=%.4f, channel=%s, devices=%s",
            frames,
            peak,
            channel,
            device_indices,
        )
        if peak < 0.001:
            log.warning(
                "DRA PCM peak near zero — on-air may be silent (check cache/TTS or announcement text)"
            )

        if _linux_use_aplay():

            def _run_aplay(index: int, program: np.ndarray, sr: int) -> None:
                alsa_candidates = _alsa_playback_devices_for_portaudio_index(index)
                pcm = _mono_f32_to_interleaved_s16(program, channel)
                used = _play_s16_stereo_via_aplay_first_working(
                    pcm,
                    sample_rate=sr,
                    alsa_devices=alsa_candidates,
                    should_abort=should_abort,
                    register_proc=register_aplay_proc,
                )
                log.info(
                    "DRA aplay → %s (%s frames, %s Hz, channel=%s, PortAudio[%s])",
                    used,
                    len(program),
                    sr,
                    channel,
                    index,
                )

            def _run_aplay_or_abort(index: int, program: np.ndarray, sr: int) -> None:
                try:
                    _run_aplay(index, program, sr)
                except RuntimeError as exc:
                    if should_abort is not None and should_abort():
                        raise TransmitAbortError("DRA USB playback aborted") from exc
                    raise

            if len(device_indices) == 1:
                _run_aplay_or_abort(device_indices[0], programs[0], samplerates[0])
            else:
                errors: list[BaseException] = []
                threads: list[threading.Thread] = []

                def _target(i: int, prog: np.ndarray, sr: int) -> None:
                    try:
                        _run_aplay_or_abort(i, prog, sr)
                    except BaseException as exc:  # noqa: BLE001
                        errors.append(exc)

                for index, program, sr in zip(device_indices, programs, samplerates):
                    thread = threading.Thread(
                        target=_target,
                        args=(index, program, sr),
                        name=f"dra-aplay-{index}",
                    )
                    threads.append(thread)
                    thread.start()
                for thread in threads:
                    thread.join()
                if errors:
                    raise errors[0]
            return

        streams: list[DraUsbPlayback] = []
        try:
            for index, sr in zip(device_indices, samplerates):
                playback = DraUsbPlayback(index, channel, sr)
                _ = playback.__enter__()
                if register_stream is not None:
                    register_stream(playback)
                streams.append(playback)
            write_programs_interleaved(streams, programs, should_abort=should_abort)
        finally:
            for playback in streams:
                playback.__exit__(None, None, None)


def play_pcm_chunks_to_single_dra(
    chunks: Iterable[PcmChunk],
    *,
    device_index: int,
    channel: str = "left",
    lead_silence: float = 0.0,
) -> None:
    """Play Piper chunks on one DRA USB output at the device's native sample rate."""
    play_pcm_chunks_to_dra_devices(
        chunks,
        device_indices=[device_index],
        channel=channel,
        lead_silence=lead_silence,
    )
