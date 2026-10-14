"""Linux ALSA: unmute CM119 Speaker so DRA USB playback is audible after reboot/replug."""

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
        "(expected PortAudio name with (hw:N,0) or CM119 in /proc/asound/cards). "
        "See docs/NUC-notes.md (ALSA mixer)."
    )


def _amixer_unmute_speaker(card: int, level: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["amixer", "-c", str(card), "sset", "Speaker", level, "unmute"],
        check=True,
        capture_output=True,
        text=True,
    )


def unmute_speaker(card: int, level: str) -> None:
    try:
        result = _amixer_unmute_speaker(card, level)
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


def try_unmute_speaker(card: int, level: str) -> bool:
    try:
        result = _amixer_unmute_speaker(card, level)
    except FileNotFoundError:
        log.warning(
            "amixer not found; cannot unmute CM119 Speaker on card %s (docs/NUC-notes.md)",
            card,
            exc_info=True,
        )
        return False
    except subprocess.CalledProcessError:
        log.warning(
            "Could not unmute ALSA Speaker on card %s (amixer sset Speaker %s unmute failed)",
            card,
            level,
            exc_info=True,
        )
        return False
    else:
        log.info(
            "Unmuted ALSA Speaker on card %s (%s)",
            card,
            (result.stdout or "").strip().split("\n")[0][:120],
        )
        return True


def try_unmute_cm119_cards(cards: list[int], *, level: str) -> bool:
    if not cards:
        return False
    return all(try_unmute_speaker(card, level) for card in cards)


class Cm119AlsaWatch:
    """Re-unmute CM119 Speaker when ALSA card numbers change (USB replug)."""

    __slots__ = ("last_unmuted_signature",)

    def __init__(self) -> None:
        self.last_unmuted_signature: tuple[int, ...] | None = None

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


def cm119_alsa_cards_for_radio(radio: object) -> list[int]:
    from app.dra_radio import DraRadio

    if not isinstance(radio, DraRadio):
        return []
    limit = max(len(radio.audio_device_indices), radio.hid_count)
    cards = alsa_cards_for_dra_outputs(radio.audio_device_indices)
    if cards:
        return cards
    return list(cm119_alsa_card_signature(limit=limit))


def apply_cm119_replug_recovery(radio: object) -> None:
    """After USB replug: drop stale HID handles, unmute Speaker, refresh signature."""
    from app.config import DRA_ALSA_SPEAKER_LEVEL, dra_alsa_unmute_enabled
    from app.dra_radio import DraRadio

    if not isinstance(radio, DraRadio):
        return
    limit = max(len(radio.audio_device_indices), radio.hid_count)
    hid_replug = radio.sync_hid_adapters_if_replugged()
    signature = alsa_watch.pending_replug_signature(limit=limit)
    if not hid_replug and signature is None:
        return
    if not hid_replug:
        radio.reset_hid_after_usb_replug()
    need_unmute = dra_alsa_unmute_enabled() and (signature is not None or hid_replug)
    if need_unmute:
        cards = (
            list(signature)
            if signature is not None
            else cm119_alsa_cards_for_radio(radio)
        )
        if cards and not try_unmute_cm119_cards(cards, level=DRA_ALSA_SPEAKER_LEVEL):
            return
        noted = signature if signature is not None else tuple(
            cm119_alsa_card_signature(limit=limit) or tuple(cards)
        )
        if noted:
            alsa_watch.note_unmuted_signature(noted)
    if signature is not None:
        log.info("CM119 USB replug recovery: card(s) %s", ", ".join(map(str, signature)))
    elif hid_replug:
        log.info("CM119 USB replug recovery: HID paths refreshed (ALSA signature unchanged)")


def maybe_sync_cm119_alsa_unmute(radio: object) -> None:
    apply_cm119_replug_recovery(radio)


def ensure_cm119_speakers_unmuted(device_indices: list[int], *, level: str) -> None:
    if not sys.platform.startswith("linux"):
        return
    cards = alsa_cards_for_dra_outputs(device_indices)
    for card in cards:
        unmute_speaker(card, level)
    alsa_watch.note_unmuted_signature(tuple(cards))
