"""Radio backend protocol (stub vs DRA).

Required: ``channel_busy``, ``set_ptt``. Optional capability Protocols and ``as_*``
helpers for clear-hold, COS polling, USB transmit, etc. are documented in
``docs/radio-backend.md``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.audio import PcmChunk
from app.config import DRA_COS_LINE, DRA_USB_CHANNEL, PTT_LEAD_SECONDS, radio_backend
from app.playback import play_chunks
from app.radio_errors import ChannelBusyError, TransmitAbortError

log = logging.getLogger("app.radio")


class Radio(Protocol):
    def channel_busy(self) -> bool: ...
    def set_ptt(self, keyed: bool) -> None: ...


# ---------------------------------------------------------------------------
# Optional capability Protocols — checked with ``as_*`` helpers, not getattr.
# ---------------------------------------------------------------------------


@runtime_checkable
class IdleCreditRadio(Protocol):
    """Radio that tracks continuous COS idle time."""

    def continuous_idle_seconds(self) -> float: ...


@runtime_checkable
class ClearWaitRadio(Protocol):
    """Radio that can block until the channel has been idle for a clear-hold period."""

    def wait_for_transmit_clear(
        self,
        *,
        clear_seconds: float,
        poll_seconds: float,
        deadline: datetime,
        now: Callable[[], datetime],
        sleep_fn: Callable[[float], None],
        fail_fast_on_busy: bool = False,
    ) -> bool: ...


@runtime_checkable
class CosSnapshotRadio(Protocol):
    """Radio that can peek COS state without opening HID (for UI/SSE status)."""

    def cos_defer_snapshot(self) -> tuple[bool | None, float]: ...


@runtime_checkable
class UsbTransmitRadio(Protocol):
    """DRA USB session: prepare, begin/end transmit window, stream PCM + lead."""

    def prepare_for_transmit(self) -> None: ...

    def begin_usb_transmit(self) -> None: ...

    def end_usb_transmit(self) -> None: ...

    def play_transmit_audio(
        self,
        chunks: Iterable[PcmChunk],
        *,
        lead_seconds: float,
    ) -> None: ...


@runtime_checkable
class UsbAbortRadio(Protocol):
    """USB-loss detection during an in-flight DRA transmit."""

    @property
    def usb_hid_present(self) -> bool: ...

    def transmit_abort_is_set(self) -> bool: ...


@runtime_checkable
class CosPollingRadio(Protocol):
    """Radio with background COS polling support (warm HID, refresh, PTT guard)."""

    def warm_open_hid(self) -> None: ...
    def refresh_cos(self) -> bool: ...

    @property
    def ptt_keyed(self) -> bool: ...


@runtime_checkable
class AudioReplugRadio(Protocol):
    """Radio that can re-resolve PortAudio indices after USB replug."""

    def sync_audio_output_indices_if_changed(
        self, *, after_usb_event: bool = False
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Typed helpers — return the narrowed Protocol or None.
# ---------------------------------------------------------------------------


def as_idle_credit(radio: Radio) -> IdleCreditRadio | None:
    return radio if isinstance(radio, IdleCreditRadio) else None


def as_clear_wait(radio: Radio) -> ClearWaitRadio | None:
    return radio if isinstance(radio, ClearWaitRadio) else None


def as_cos_snapshot(radio: Radio) -> CosSnapshotRadio | None:
    return radio if isinstance(radio, CosSnapshotRadio) else None


def as_usb_transmit(radio: Radio) -> UsbTransmitRadio | None:
    return radio if isinstance(radio, UsbTransmitRadio) else None


def as_usb_abort(radio: Radio) -> UsbAbortRadio | None:
    return radio if isinstance(radio, UsbAbortRadio) else None


def as_cos_polling(radio: Radio) -> CosPollingRadio | None:
    return radio if isinstance(radio, CosPollingRadio) else None


def as_audio_replug(radio: Radio) -> AudioReplugRadio | None:
    return radio if isinstance(radio, AudioReplugRadio) else None


def usb_transmit_lost(radio: Radio) -> bool:
    """True when CM119 USB dropped during an in-flight DRA transmit."""
    abort = as_usb_abort(radio)
    if abort is None:
        return False
    return (not abort.usb_hid_present) or abort.transmit_abort_is_set()


def channel_blocks_transmit(radio: Radio, *, clear_seconds: float = 0) -> bool:
    """True if COS says we must not key yet; skips the COS read once the clear hold is met.

    Not a final pre-PTT check — ``fire._confirm_clear_before_ptt`` always reads COS fresh.
    """
    idle = as_idle_credit(radio)
    if clear_seconds > 0 and idle is not None and idle.continuous_idle_seconds() >= clear_seconds:
        return False
    return radio.channel_busy()


class StubRadio:
    """In-memory SQL/PTT stand-in when ``TTS_RADIO_BACKEND=stub`` (default)."""

    busy: bool
    ptt: bool
    events: list[tuple[str, object]]
    busy_sequence: list[bool] | None
    _busy_index: int

    def __init__(self) -> None:
        self.busy = False
        self.ptt = False
        self.events = []
        self.busy_sequence = None
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

    def cos_defer_snapshot(self) -> tuple[bool | None, float]:
        """Peek COS for UI/SSE without consuming ``busy_sequence`` (matches DRA snapshot path)."""
        if self.busy_sequence is not None:
            index = min(self._busy_index, len(self.busy_sequence) - 1)
            return bool(self.busy_sequence[index]), 0.0
        return self.busy, 0.0

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
    on_ptt_keyed: Callable[[], None] | None = None,
) -> None:
    import time

    play = play_fn or play_chunks
    sleep = sleep_fn or time.sleep
    lead = PTT_LEAD_SECONDS if lead_seconds is None else lead_seconds
    usb = as_usb_transmit(radio)

    if channel_blocks_transmit(radio, clear_seconds=clear_seconds):
        raise ChannelBusyError("channel busy immediately before PTT")

    def _key() -> None:
        radio.set_ptt(True)
        if on_ptt_keyed is not None:
            on_ptt_keyed()

    if usb is not None:
        usb.prepare_for_transmit()
        usb.begin_usb_transmit()
        try:
            _key()
            try:
                # Lead must be the same continuous USB stream as speech. A separate
                # lead aplay exits before speech starts, COMM OK drops, and RF unkeys
                # briefly even though GPIO3 PTT stays asserted.
                usb.play_transmit_audio(chunks, lead_seconds=lead)
            finally:
                radio.set_ptt(False)
        except TransmitAbortError:
            raise
        except RuntimeError as exc:
            if usb_transmit_lost(radio):
                raise TransmitAbortError("DRA USB playback aborted") from exc
            raise
        finally:
            usb.end_usb_transmit()
        return

    # play locally, not on-air
    _key()
    try:
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
