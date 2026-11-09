"""PortAudio device enumeration and index helpers for DRA USB audio."""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import NotRequired, TypedDict, cast

log = logging.getLogger("app.dra_portaudio")

portaudio_lock = threading.RLock()

from app.dra_device_names import is_dra_audio_name


class PortAudioDeviceInfo(TypedDict):
    """Fields we read from ``sounddevice.query_devices()`` entries."""

    name: str
    max_output_channels: int
    max_input_channels: NotRequired[int]
    default_samplerate: NotRequired[float]
    hostapi: NotRequired[int]


DraOutputDevice = tuple[int, PortAudioDeviceInfo]


def _as_portaudio_device(raw: object) -> PortAudioDeviceInfo:
    if not isinstance(raw, dict):
        raise TypeError(f"PortAudio device info must be a mapping, got {type(raw)!r}")
    mapping = cast(dict[object, object], raw)
    name = mapping.get("name")
    if not isinstance(name, str):
        raise TypeError(f"PortAudio device missing name: {raw!r}")
    max_out = mapping.get("max_output_channels")
    info: PortAudioDeviceInfo = {
        "name": name,
        "max_output_channels": max_out if isinstance(max_out, int) else 0,
    }
    max_in = mapping.get("max_input_channels")
    if isinstance(max_in, int):
        info["max_input_channels"] = max_in
    rate = mapping.get("default_samplerate")
    if isinstance(rate, (int, float)):
        info["default_samplerate"] = float(rate)
    hostapi = mapping.get("hostapi")
    if isinstance(hostapi, int):
        info["hostapi"] = hostapi
    return info


def _ensure_portaudio_initialized_unlocked() -> None:
    import sounddevice as sd
    from sounddevice import PortAudioError

    # sounddevice has no public re-init API; Pa_* lives on private helpers.
    check = cast(Callable[[object], object], getattr(sd, "_check"))
    lib = cast(object, getattr(sd, "_lib"))
    get_count = cast(Callable[[], object], getattr(lib, "Pa_GetDeviceCount"))
    initialize = cast(Callable[[], None], getattr(sd, "_initialize"))
    try:
        _ = check(get_count())
    except PortAudioError:
        initialize()


def refresh_portaudio_device_list() -> None:
    """Re-enumerate PortAudio devices after Linux USB hot-plug (list is cached at init)."""
    if not sys.platform.startswith("linux"):
        return
    import sounddevice as sd

    terminate = cast(Callable[[], None], getattr(sd, "_terminate"))
    initialize = cast(Callable[[], None], getattr(sd, "_initialize"))
    with portaudio_lock:
        try:
            terminate()
        except Exception:
            log.debug("PortAudio terminate before replug refresh", exc_info=True)
        try:
            initialize()
        except Exception:
            log.warning("PortAudio re-init after USB replug failed", exc_info=True)
            try:
                initialize()
            except Exception:
                log.error("PortAudio initialize recovery failed", exc_info=True)


def list_dra_output_devices() -> list[DraOutputDevice]:
    import sounddevice as sd

    with portaudio_lock:
        _ensure_portaudio_initialized_unlocked()
        out: list[DraOutputDevice] = []
        for index, raw_device in enumerate(cast(list[object], cast(object, sd.query_devices()))):
            try:
                dev = _as_portaudio_device(raw_device)
            except TypeError:
                continue
            if dev["max_output_channels"] < 1:
                continue
            if is_dra_audio_name(dev["name"]):
                out.append((index, dev))
        return out


def _is_alsa_hw_portaudio_name(name: str) -> bool:
    """True for Linux ALSA hardware devices, e.g. ``… (hw:1,0)``."""
    return "(hw:" in name


def _is_pipewire_usb_sink_name(name: str) -> bool:
    """PipeWire/Pulse PortAudio sink for a USB sound card (not raw ``hw:``)."""
    lowered = name.lower()
    return "alsa_output.usb" in lowered and is_dra_audio_name(name)


def prefer_cm119_hw_devices(
    hw_dra: list[DraOutputDevice],
) -> list[DraOutputDevice]:
    """Drop non-CM119 ``(hw:…)`` entries when /proc lists CM119 cards (avoids stale indices)."""
    from app.dra_alsa_unmute import alsa_card_from_portaudio_device, cm119_alsa_card_signature

    cards = set(cm119_alsa_card_signature(limit=max(len(hw_dra), 1)))
    if not cards:
        return hw_dra
    matched = [
        (index, dev)
        for index, dev in hw_dra
        if alsa_card_from_portaudio_device(index) in cards
    ]
    return matched if matched else hw_dra


def auto_select_dra_outputs(
    devices: list[DraOutputDevice] | None = None,
) -> list[DraOutputDevice]:
    """DRA-like outputs for auto pairing (no ``TTS_DRA_AUDIO_DEVICES``).

    On a Linux NUC with PipeWire, the same CM119 often appears as both an ALSA
    ``(hw:…)`` device and an ``alsa_output.usb-…`` sink. Default ``(hw:…)`` —
    the PipeWire sink can hang ``sd.play()`` / PortAudio. Set
    ``TTS_DRA_PREFER_ALSA_HW=0`` to prefer ``alsa_output.usb-…``. When no
    ``(hw:…)`` names exist (typical macOS), return every DRA-like output.
    """
    from app.config import dra_prefer_alsa_hw

    all_dra = devices if devices is not None else list_dra_output_devices()
    pw_usb = [
        (index, dev)
        for index, dev in all_dra
        if _is_pipewire_usb_sink_name(str(dev["name"]))
    ]
    hw = [(index, dev) for index, dev in all_dra if _is_alsa_hw_portaudio_name(dev["name"])]
    if pw_usb and not dra_prefer_alsa_hw():
        if not hw or len(pw_usb) >= len(prefer_cm119_hw_devices(hw)):
            return pw_usb
    if hw:
        return prefer_cm119_hw_devices(hw)
    return all_dra


def resolve_dra_audio_indices(
    *,
    device_specs: list[str] | None = None,
) -> list[int]:
    """Return PortAudio output indices for each DRA.

    *device_specs* — optional substrings or integer indices (one per DRA). When omitted,
    uses :func:`auto_select_dra_outputs` (ALSA ``hw`` devices when present).
    """
    import sounddevice as sd

    all_dra = list_dra_output_devices()
    if not all_dra:
        raise RuntimeError(
            "No DRA USB audio device found (look for C-Media / USB PnP Sound Device in PortAudio)"
        )

    if not device_specs:
        selected = auto_select_dra_outputs(all_dra)
        for index, dev in selected:
            name = str(dev["name"])
            if not is_dra_audio_name(name):
                raise RuntimeError(
                    f"Resolved PortAudio device [{index}] is not a DRA output: {name!r}"
                )
        return [index for index, _dev in selected]

    indices: list[int] = []
    for spec in device_specs:
        spec = spec.strip()
        if not spec:
            continue
        if spec.isdigit():
            idx = int(spec)
            with portaudio_lock:
                _ensure_portaudio_initialized_unlocked()
                dev = _as_portaudio_device(cast(object, sd.query_devices(idx)))
            if dev["max_output_channels"] < 1:
                raise RuntimeError(f"TTS_DRA_AUDIO_DEVICES index {idx} has no output channels")
            indices.append(idx)
            continue
        matches = [(i, d) for i, d in all_dra if spec.lower() in d["name"].lower()]
        if len(matches) != 1:
            names = [d["name"] for _i, d in all_dra]
            raise RuntimeError(
                f"TTS_DRA_AUDIO_DEVICES entry {spec!r} matched {len(matches)} devices "
                + f"(expected 1). Available: {names}"
            )
        indices.append(matches[0][0])
    if not indices:
        raise RuntimeError("TTS_DRA_AUDIO_DEVICES was set but parsed empty")
    return indices


def portaudio_index_is_dra_output(device_index: int) -> bool:
    try:
        return is_dra_audio_name(portaudio_output_device_name(device_index))
    except Exception:
        return False


def portaudio_output_device_name(device_index: int) -> str:
    import sounddevice as sd

    with portaudio_lock:
        _ensure_portaudio_initialized_unlocked()
        return _as_portaudio_device(cast(object, sd.query_devices(device_index)))["name"]


def _alsa_hw_samplerate(card: int, subdevice: int = 0) -> int | None:
    """Hardware rate for ``hw:C,D`` from ``arecord --dump-hw-params`` (often 48000 on NUC)."""
    device = f"hw:{card},{subdevice}"
    try:
        proc = subprocess.run(
            ["arecord", "-D", device, "--dump-hw-params"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    text = f"{proc.stdout}\n{proc.stderr}"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("RATE:"):
            parts = stripped.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def dra_device_samplerate(device_index: int) -> int:
    """Sample rate for DRA USB playback (ALSA hw rate when it differs from PortAudio)."""
    import sounddevice as sd

    with portaudio_lock:
        _ensure_portaudio_initialized_unlocked()
        info = _as_portaudio_device(cast(object, sd.query_devices(device_index)))
        pa_rate = int(info.get("default_samplerate") or 48000)
    if sys.platform.startswith("linux"):
        name = portaudio_output_device_name(device_index)
        if _is_alsa_hw_portaudio_name(name):
            from app.dra_alsa_unmute import alsa_card_from_portaudio_device

            card = alsa_card_from_portaudio_device(device_index)
            if card is not None:
                hw_rate = _alsa_hw_samplerate(card, 0)
                if hw_rate is not None and hw_rate != pa_rate:
                    log.info(
                        "DRA sample rate: PortAudio[%s] reports %s Hz; using ALSA hw %s Hz",
                        device_index,
                        pa_rate,
                        hw_rate,
                    )
                    return hw_rate
    return pa_rate
