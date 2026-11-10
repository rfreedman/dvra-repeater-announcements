"""Linux ALSA unmute helpers for CM119 (no DRA radio imports — avoids cycles)."""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

from app.dra_device_names import is_dra_audio_name
from app.startup_errors import StartupError

log = logging.getLogger("app.dra_alsa_unmute")

_HW_CARD_RE = re.compile(r"\(hw:(\d+),")


def alsa_card_from_portaudio_device(device_index: int) -> int | None:
    import sounddevice as sd

    raw = cast(object, sd.query_devices(device_index))
    if not isinstance(raw, dict):
        return None
    name = cast(dict[object, object], raw).get("name")
    if not isinstance(name, str):
        return None
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


def cm119_alsa_card_signature(*, limit: int) -> tuple[int, ...]:
    """Sorted ALSA card numbers for CM119 devices currently in /proc/asound/cards."""
    return tuple(_cm119_cards_from_proc(limit=limit))


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
        + "(expected PortAudio name with (hw:N,0) or CM119 in /proc/asound/cards). "
        + "See docs/linux-host.md (ALSA mixer)."
    )


_MIXER_CONTROL_RE = re.compile(r"Simple mixer control '([^']+)'", re.MULTILINE)
_SKIP_MIXER_KEYWORDS = ("mic", "capture", "record", "input")


def _playback_mixer_controls(card: int) -> list[str]:
    try:
        proc = subprocess.run(
            ["amixer", "-c", str(card), "scontents"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ["Speaker"]
    stdout = str(proc.stdout or "")
    names = cast(list[str], _MIXER_CONTROL_RE.findall(stdout))
    playback = [
        name
        for name in names
        if not any(key in name.lower() for key in _SKIP_MIXER_KEYWORDS)
    ]
    return playback or ["Speaker"]


def _amixer_sset(card: int, control: str, level: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["amixer", "-c", str(card), "sset", control, level, "unmute"],
        check=True,
        capture_output=True,
        text=True,
    )


def _amixer_unmute_speaker(card: int, level: str) -> subprocess.CompletedProcess[str]:
    return _amixer_sset(card, "Speaker", level)


def unmute_speaker(card: int, level: str) -> None:
    try:
        result = _amixer_unmute_speaker(card, level)
    except FileNotFoundError as exc:
        raise StartupError(
            "amixer not found; install alsa-utils on the Linux host to unmute CM119 Speaker "
            + "(docs/linux-host.md)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        err_text = cast(object, exc.stderr)
        out_text = cast(object, exc.stdout)
        detail = str(err_text or out_text or exc).strip()
        raise StartupError(
            f"Could not unmute ALSA Speaker on card {card} "
            + f"(amixer sset Speaker {level} unmute failed). {detail} "
            + "See docs/linux-host.md."
        ) from exc
    else:
        log.info(
            "Unmuted ALSA Speaker on card %s (%s)",
            card,
            (result.stdout or "").strip().split("\n")[0][:120],
        )


def try_unmute_speaker(card: int, level: str) -> bool:
    return try_unmute_playback_controls(card, level)


def try_unmute_playback_controls(card: int, level: str) -> bool:
    controls = _playback_mixer_controls(card)
    ok = True
    for control in controls:
        try:
            result = _amixer_sset(card, control, level)
        except FileNotFoundError:
            log.warning(
                "amixer not found; cannot unmute CM119 on card %s (docs/linux-host.md)",
                card,
                exc_info=True,
            )
            return False
        except subprocess.CalledProcessError as exc:
            err_text = cast(object, exc.stderr)
            out_text = cast(object, exc.stdout)
            detail = str(err_text or out_text or exc).strip()
            log.warning(
                "Could not unmute ALSA %r on card %s (amixer sset %s %s unmute failed): %s",
                control,
                card,
                control,
                level,
                detail[:200],
            )
            ok = False
            continue
        log.info(
            "Unmuted ALSA %s on card %s (%s)",
            control,
            card,
            (result.stdout or "").strip().split("\n")[0][:120],
        )
    return ok


def try_unmute_cm119_cards(cards: list[int], *, level: str) -> bool:
    if not cards:
        return False
    return all(try_unmute_playback_controls(card, level) for card in cards)


class Cm119AlsaWatch:
    """Re-unmute CM119 Speaker when ALSA card numbers change (USB replug)."""

    __slots__: tuple[str, ...] = ("last_unmuted_signature",)
    last_unmuted_signature: tuple[int, ...] | None

    def __init__(self) -> None:
        self.last_unmuted_signature = None

    def note_unmuted_signature(self, signature: tuple[int, ...]) -> None:
        self.last_unmuted_signature = signature

    def pending_replug_signature(self, *, limit: int) -> tuple[int, ...] | None:
        if not sys.platform.startswith("linux") or limit <= 0:
            return None
        signature = cm119_alsa_card_signature(limit=limit)
        if not signature or signature == self.last_unmuted_signature:
            return None
        return signature


alsa_watch = Cm119AlsaWatch()

def sync_cm119_speaker_unmute_for_indices(device_indices: list[int]) -> None:
    """Best-effort Speaker unmute from PortAudio output indices (playback path)."""
    from app.config import DRA_ALSA_SPEAKER_LEVEL, dra_alsa_unmute_enabled

    if not sys.platform.startswith("linux") or not dra_alsa_unmute_enabled():
        return
    if not device_indices:
        return
    try:
        cards = alsa_cards_for_dra_outputs(device_indices)
    except StartupError:
        log.warning(
            "CM119 Speaker unmute skipped: could not map PortAudio indices %s to ALSA cards",
            device_indices,
            exc_info=True,
        )
        return
    if not try_unmute_cm119_cards(cards, level=DRA_ALSA_SPEAKER_LEVEL):
        log.warning("CM119 Speaker unmute failed on card(s) %s", cards)


def ensure_cm119_speakers_unmuted(device_indices: list[int], *, level: str) -> None:
    if not sys.platform.startswith("linux"):
        return
    cards = alsa_cards_for_dra_outputs(device_indices)
    for card in cards:
        unmute_speaker(card, level)
    alsa_watch.note_unmuted_signature(tuple(cards))
