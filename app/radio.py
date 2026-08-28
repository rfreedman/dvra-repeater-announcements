from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from app.audio import PcmChunk
from app.config import PTT_LEAD_SECONDS
from app.playback import play_chunks


class Radio(Protocol):
    def channel_busy(self) -> bool: ...
    def set_ptt(self, keyed: bool) -> None: ...


class StubRadio:
    """In-memory SQL/PTT stand-in. GPIO comes later."""

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
) -> None:
    import time

    play = play_fn or play_chunks
    sleep = sleep_fn or time.sleep
    lead = PTT_LEAD_SECONDS if lead_seconds is None else lead_seconds
    radio.set_ptt(True)
    try:
        if lead > 0:
            sleep(lead)
        play(chunks)
    finally:
        radio.set_ptt(False)


_radio: Radio | None = None


def get_radio() -> Radio:
    global _radio
    if _radio is None:
        _radio = StubRadio()
    return _radio


def set_radio(radio: Radio | None) -> None:
    global _radio
    _radio = radio
