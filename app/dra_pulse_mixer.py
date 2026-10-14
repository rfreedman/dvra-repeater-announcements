"""PipeWire/PulseAudio sink volume for CM119 PortAudio ``alsa_output.usb-…`` devices."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger("app.dra_pulse_mixer")


def _pactl(*args: str) -> subprocess.CompletedProcess[str] | None:
    if shutil.which("pactl") is None:
        return None
    try:
        return subprocess.run(
            ["pactl", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        log.debug("pactl failed", exc_info=True)
        return None


def sync_pulse_sink_unmute_for_portaudio_name(device_name: str) -> None:
    """Unmute and raise volume on a PipeWire/Pulse sink (ALSA Speaker is not enough)."""
    if not sys.platform.startswith("linux"):
        return
    if "alsa_output." not in device_name:
        return
    unmute = _pactl("set-sink-mute", device_name, "0")
    volume = _pactl("set-sink-volume", device_name, "100%")
    if unmute is None or volume is None:
        log.debug("pactl not available; skip Pulse sink unmute for %s", device_name)
        return
    if unmute.returncode != 0 or volume.returncode != 0:
        log.warning(
            "Pulse sink unmute/volume failed for %s (mute rc=%s vol rc=%s)",
            device_name,
            unmute.returncode if unmute else "?",
            volume.returncode if volume else "?",
        )
    else:
        log.info("Pulse sink unmuted at 100%%: %s", device_name)


def sync_pulse_sink_unmute_for_portaudio_index(device_index: int) -> None:
    from app.dra_playback import portaudio_output_device_name

    try:
        name = portaudio_output_device_name(device_index)
    except Exception:
        log.debug("Could not resolve PortAudio name for index %s", device_index, exc_info=True)
        return
    sync_pulse_sink_unmute_for_portaudio_name(name)
