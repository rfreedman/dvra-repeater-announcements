"""The final COS check before PTT must read COS fresh, not trust accrued idle credit."""
from __future__ import annotations

from app.fire import handle_fire
from app.radio import StubRadio, channel_blocks_transmit
from tests.test_fire import _announcement, _ctx, _deps


class IdleCreditRadio(StubRadio):
    def __init__(self, *, idle_seconds: float, busy_reads: list[bool]) -> None:
        super().__init__()
        self._idle_seconds = idle_seconds
        self._busy_reads = list(busy_reads)

    def continuous_idle_seconds(self) -> float:
        return self._idle_seconds

    def channel_busy(self) -> bool:
        busy = self._busy_reads.pop(0) if self._busy_reads else False
        self.events.append(("busy_check", busy))
        return busy


def test_channel_blocks_transmit_skips_read_when_clear_hold_met():
    radio = IdleCreditRadio(idle_seconds=10.0, busy_reads=[True, True])
    assert channel_blocks_transmit(radio, clear_seconds=10.0) is False
    assert channel_blocks_transmit(radio, clear_seconds=11.0) is True


def test_busy_read_before_ptt_defers_even_with_idle_credit():
    radio = IdleCreditRadio(idle_seconds=12.0, busy_reads=[True])
    deps, extras = _deps(_announcement(), radio, dra_clear_seconds=10.0)

    assert handle_fire(_ctx(), deps) == "deferred"
    assert not extras["played"]
    assert ("ptt", True) not in radio.events


def test_key_up_during_final_check_window_defers():
    radio = IdleCreditRadio(idle_seconds=12.0, busy_reads=[False, False, False, True])
    deps, extras = _deps(
        _announcement(),
        radio,
        dra_clear_seconds=10.0,
        dra_clear_poll_seconds=0.25,
        dra_final_check_seconds=1.0,
    )

    assert handle_fire(_ctx(), deps) == "deferred"
    assert not extras["played"]


def test_final_check_window_reads_cos_for_its_full_length_then_transmits():
    radio = IdleCreditRadio(idle_seconds=12.0, busy_reads=[])
    deps, extras = _deps(
        _announcement(),
        radio,
        dra_clear_seconds=10.0,
        dra_clear_poll_seconds=0.25,
        dra_final_check_seconds=1.0,
    )

    assert handle_fire(_ctx(), deps) == "transmitted"
    assert extras["played"]
    assert sum(1 for event in radio.events if event[0] == "busy_check") == 5
