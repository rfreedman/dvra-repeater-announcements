"""Linux ALSA: unmute CM119 Speaker so DRA USB playback is audible after reboot."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

from app.dra_playback import is_dra_audio_name
from app.startup import StartupError

log = logging.getLogger("app.dra_alsa_mixer")

_HW_CARD_RE = re.compile(r"\(hw:(\d+),")


def alsa_card_from_portaudio_device(device_index: int) -> int | None:
    import sounddevice as sd

    name = str(sd.query_devices(device_index)["name"])
    match = _HW_CARD_RE.search(name)
    if match:
        return int(match.group(1))
    return None


def _cm119_cards_from_proc(*, limit: int) -> list[int]:
    path = Path("/proc/asound/cards")
    if not path.is_file():
        return []
    cards: list[int] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        card_str, _, rest = stripped.partition(" ")
        if not card_str.isdigit():
            continue
        if not is_dra_audio_name(rest):
            continue
        cards.append(int(card_str))
    return cards[: max(limit, 0)] if limit else cards


def alsa_cards_for_dra_outputs(device_indices: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for index in device_indices:
        card = alsa_card_from_portaudio_device(index)
        if card is None or card in seen:
            continue
        seen.add(card)
        ordered.append(card)
    if ordered:
        return ordered
    if not device_indices:
        return []
    fallback = _cm119_cards_from_proc(limit=len(device_indices))
    if fallback:
        return fallback
    raise StartupError(
        "Could not determine ALSA card number for DRA USB audio "
        "(expected PortAudio name with (hw:N,0) or CM119 in /proc/asound/cards). "
        "See docs/NUC-notes.md (ALSA mixer)."
    )


def unmute_speaker(card: int, level: str) -> None:
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "sset", "Speaker", level, "unmute"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise StartupError(
            "amixer not found; install alsa-utils on the NUC to unmute CM119 Speaker "
            "(docs/NUC-notes.md)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise StartupError(
            f"Could not unmute ALSA Speaker on card {card} "
            f"(amixer sset Speaker {level} unmute failed). {detail} "
            "See docs/NUC-notes.md."
        ) from exc
    else:
        log.info(
            "Unmuted ALSA Speaker on card %s (%s)",
            card,
            (result.stdout or "").strip().split("\n")[0][:120],
        )


def ensure_cm119_speakers_unmuted(device_indices: list[int], *, level: str) -> None:
    if not sys.platform.startswith("linux"):
        return
    cards = alsa_cards_for_dra_outputs(device_indices)
    for card in cards:
        unmute_speaker(card, level)
