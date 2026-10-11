"""Clear-hold idle credit must allow transmit when COS samples flap busy."""
from __future__ import annotations

from app.audio import PcmChunk
from app.fire import FireContext, handle_fire
from app.radio import StubRadio, channel_blocks_transmit
from tests.test_fire import _announcement, _ctx, _deps


class IdleCreditRadio(StubRadio):
    def __init__(self, *, idle_seconds: float, busy_reads: bool = True) -> None:
        super().__init__()
        self._idle_seconds = idle_seconds
        self._busy_reads = busy_reads

    def continuous_idle_seconds(self) -> float:
        return self._idle_seconds

    def channel_busy(self) -> bool:
        self.events.append(("busy_check", self._busy_reads))
        return self._busy_reads


def test_channel_blocks_transmit_respects_clear_hold():
    radio = IdleCreditRadio(idle_seconds=10.0, busy_reads=True)
    assert channel_blocks_transmit(radio, clear_seconds=10.0) is False
    assert channel_blocks_transmit(radio, clear_seconds=11.0) is True


def test_fire_transmits_when_clear_hold_met_despite_busy_sample():
    radio = IdleCreditRadio(idle_seconds=12.0, busy_reads=True)
    deps, extras = _deps(_announcement(), radio, dra_clear_seconds=10.0)

    assert handle_fire(_ctx(), deps) == "transmitted"
    assert extras["played"]
    assert ("ptt", True) in radio.events
