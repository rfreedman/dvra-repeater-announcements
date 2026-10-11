from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Protocol

from app.audio import PcmChunk
from app.config import DRA_COS_LINE, DRA_USB_CHANNEL, PTT_LEAD_SECONDS, radio_backend
from app.playback import play_chunks

log = logging.getLogger("app.radio")


class ChannelBusyError(Exception):
    """Raised when COS is active immediately before PTT (should not key)."""


class Radio(Protocol):
    def channel_busy(self) -> bool: ...
    def set_ptt(self, keyed: bool) -> None: ...


def channel_blocks_transmit(radio: Radio, *, clear_seconds: float = 0) -> bool:
    """True if COS says we must not key yet (clear hold overrides flaky busy samples)."""
    idle_fn = getattr(radio, "continuous_idle_seconds", None)
    if clear_seconds > 0 and idle_fn is not None and idle_fn() >= clear_seconds:
        return False
    return radio.channel_busy()


class StubRadio:
    """In-memory SQL/PTT stand-in when ``TTS_RADIO_BACKEND=stub`` (default)."""

    def __init__(self) -> None:
        self.busy = False
        self.ptt = False
        self.events: list[tuple[str, object]] = []
        self.busy_sequence: list[bool] | None = None
        self._busy_index = 0

    def channel_busy(self) -> bool:
        if self.busy_sequence is not None:
            index = min(self._busy_index, len(self.busy_sequence) - 1)
            value = bool(self.busy_sequence[index])
            self._busy_index += 1
        else:
            value = self.busy
        self.events.append(("busy_check", value))
        return value

    def set_ptt(self, keyed: bool) -> None:
        self.ptt = bool(keyed)
        self.events.append(("ptt", self.ptt))


def transmit(
    chunks: Iterable[PcmChunk],
    *,
    radio: Radio,
    play_fn: Callable[[Iterable[PcmChunk]], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    lead_seconds: float | None = None,
    clear_seconds: float = 0,
) -> None:
    import time

    play = play_fn or play_chunks
    sleep = sleep_fn or time.sleep
    lead = PTT_LEAD_SECONDS if lead_seconds is None else lead_seconds
    play_on_dra = getattr(radio, "play_transmit_audio", None)

    if channel_blocks_transmit(radio, clear_seconds=clear_seconds):
        raise ChannelBusyError("channel busy immediately before PTT")

    radio.set_ptt(True)
    try:
        if play_on_dra is not None:
            play_on_dra(chunks, lead_seconds=lead)
        else:
            if lead > 0:
                sleep(lead)
            play(chunks)
    finally:
        radio.set_ptt(False)


def create_radio_from_config() -> Radio:
    backend = radio_backend()
    if backend in ("", "stub"):
        return StubRadio()
    if backend in ("dra", "cm119"):
        from app.dra_radio import DraRadio

        radio = DraRadio.from_config()
        log.info(
            "DRA radio backend: %s HID path(s), audio PortAudio %s, USB channel=%s, cos=%s",
            radio.hid_count,
            radio.audio_device_indices,
            DRA_USB_CHANNEL,
            DRA_COS_LINE,
        )
        return radio
    raise RuntimeError(
        f"Unknown TTS_RADIO_BACKEND={backend!r} (use stub or dra)"
    )


_radio: Radio | None = None


def get_radio() -> Radio:
    global _radio
    if _radio is None:
        _radio = create_radio_from_config()
    return _radio


def set_radio(radio: Radio | None) -> None:
    global _radio
    _radio = radio


def radio_mode_label(radio: Radio) -> str:
    if isinstance(radio, StubRadio):
        return "stub"
    from app.dra_radio import DraRadio

    if isinstance(radio, DraRadio):
        return "dra"
    return type(radio).__name__.lower()


def active_radio_mode_label() -> str:
    return radio_mode_label(get_radio())
