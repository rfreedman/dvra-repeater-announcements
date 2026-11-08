"""Linux ALSA: CM119 Speaker unmute + USB replug recovery (DRA radio aware)."""

from __future__ import annotations

import logging
import sys
from typing import Protocol, cast, runtime_checkable

from app.dra_alsa_unmute import (
    alsa_card_from_portaudio_device as alsa_card_from_portaudio_device,
    alsa_cards_for_dra_outputs,
    alsa_watch,
    cm119_alsa_card_signature,
    ensure_cm119_speakers_unmuted as ensure_cm119_speakers_unmuted,
    sync_cm119_speaker_unmute_for_indices as sync_cm119_speaker_unmute_for_indices,
    try_unmute_cm119_cards,
    try_unmute_speaker as try_unmute_speaker,
    unmute_speaker as unmute_speaker,
)

log = logging.getLogger("app.dra_alsa_mixer")


@runtime_checkable
class _DraRadioLike(Protocol):
    @property
    def audio_device_indices(self) -> list[int]: ...

    @property
    def hid_count(self) -> int: ...

    @property
    def ptt_keyed(self) -> bool: ...

    @property
    def usb_hid_reconnected_pending(self) -> bool: ...

    def runtime_usb_hid_tracking_enabled(self) -> bool: ...

    def note_usb_hid_presence(self) -> bool: ...

    def sync_hid_adapters_if_replugged(self) -> bool: ...

    def sync_audio_output_indices_if_changed(self, *, after_usb_event: bool = False) -> bool: ...

    def reset_hid_after_usb_replug(self) -> None: ...

    def clear_usb_hid_reconnected_pending(self) -> None: ...

    def audio_output_indices_look_valid(self) -> bool: ...

    def prepare_for_transmit(self) -> None: ...


def _as_dra_radio(radio: object) -> _DraRadioLike | None:
    candidate = cast(object, getattr(radio, "_inner", radio))
    return candidate if isinstance(candidate, _DraRadioLike) else None


def cm119_alsa_cards_to_unmute(
    radio: object,
    *,
    limit: int,
    signature: tuple[int, ...] | None = None,
) -> list[int]:
    if signature is not None:
        return list(signature)
    cards = list(cm119_alsa_card_signature(limit=limit))
    if cards:
        return cards
    return cm119_alsa_cards_for_radio(radio)


def cm119_alsa_cards_for_radio(radio: object) -> list[int]:
    dra = _as_dra_radio(radio)
    if dra is None:
        return []
    limit = max(len(dra.audio_device_indices), dra.hid_count)
    cards = alsa_cards_for_dra_outputs(dra.audio_device_indices)
    if cards:
        return cards
    return list(cm119_alsa_card_signature(limit=limit))


def apply_cm119_replug_recovery(radio: object) -> None:
    """After USB replug: drop stale HID handles, unmute Speaker, refresh signature."""
    from app.config import DRA_ALSA_SPEAKER_LEVEL, dra_alsa_unmute_enabled

    radio = _as_dra_radio(radio)
    if radio is None:
        return
    if not radio.runtime_usb_hid_tracking_enabled():
        return
    if not radio.note_usb_hid_presence():
        return
    if radio.ptt_keyed:
        return
    reconnected = radio.usb_hid_reconnected_pending
    limit = max(len(radio.audio_device_indices), radio.hid_count)
    hid_replug = radio.sync_hid_adapters_if_replugged()
    signature = alsa_watch.pending_replug_signature(limit=limit)
    pa_rescan = reconnected or hid_replug
    audio_resync = radio.sync_audio_output_indices_if_changed(after_usb_event=pa_rescan)
    if signature is not None and not audio_resync:
        audio_resync = radio.sync_audio_output_indices_if_changed(after_usb_event=False)
    if not hid_replug and signature is None and not reconnected and not audio_resync:
        return
    if not hid_replug:
        radio.reset_hid_after_usb_replug()
    need_unmute = dra_alsa_unmute_enabled() and (
        signature is not None or hid_replug or reconnected or audio_resync
    )
    if need_unmute:
        cards = cm119_alsa_cards_to_unmute(radio, limit=limit, signature=signature)
        if cards and not try_unmute_cm119_cards(cards, level=DRA_ALSA_SPEAKER_LEVEL):
            return
        noted = signature if signature is not None else tuple(
            cm119_alsa_card_signature(limit=limit) or tuple(cards)
        )
        if noted:
            alsa_watch.note_unmuted_signature(noted)
    radio.clear_usb_hid_reconnected_pending()
    if signature is not None:
        log.info("CM119 USB replug recovery: card(s) %s", ", ".join(map(str, signature)))
    elif reconnected or hid_replug or audio_resync:
        log.info(
            "CM119 USB replug recovery: HID/audio restored (reconnected=%s hid=%s audio=%s)",
            reconnected,
            hid_replug,
            audio_resync,
        )


def sync_cm119_speaker_unmute(radio: object) -> None:
    """Best-effort CM119 Speaker unmute (always before transmit on Linux DRA)."""
    from app.config import DRA_ALSA_SPEAKER_LEVEL, dra_alsa_unmute_enabled

    dra = _as_dra_radio(radio)
    if not sys.platform.startswith("linux") or dra is None:
        return
    if not dra_alsa_unmute_enabled():
        return
    limit = max(len(dra.audio_device_indices), dra.hid_count)
    cards = cm119_alsa_cards_to_unmute(dra, limit=limit, signature=None)
    if not cards:
        log.warning(
            "CM119 Speaker unmute skipped: no ALSA cards (PortAudio indices=%s)",
            dra.audio_device_indices,
        )
        return
    if not try_unmute_cm119_cards(cards, level=DRA_ALSA_SPEAKER_LEVEL):
        log.warning("CM119 Speaker unmute failed on card(s) %s", cards)


def prepare_dra_for_transmit(radio: object) -> None:
    """Before PTT: PortAudio re-scan and DRA output indices (USB hotplug path)."""
    dra = _as_dra_radio(radio)
    if dra is not None:
        dra.prepare_for_transmit()


def finalize_dra_transmit_audio_path(radio: object) -> None:
    prepare_dra_for_transmit(radio)


def maybe_sync_cm119_alsa_unmute(radio: object) -> None:
    apply_cm119_replug_recovery(radio)
