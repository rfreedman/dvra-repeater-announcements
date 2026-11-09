from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from app.audio import PcmChunk
from app.fire import handle_fire
from app.radio import ChannelBusyError, transmit

from tests.fire_helpers import make_announcement, make_ctx, make_deps

import pytest
TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=TZ)


class GatingRadio:
    """Simulates DRA gating: busy_sequence consumed on each channel_busy()."""

    _seq: list[bool]
    wait_calls: int

    def __init__(self, busy_sequence: list[bool]) -> None:
        self._seq = list(busy_sequence)
        self.ptt_events: list[bool] = []
        self.wait_calls = 0

    def channel_busy(self) -> bool:
        if not self._seq:
            return False
        return self._seq.pop(0)

    def set_ptt(self, keyed: bool) -> None:
        self.ptt_events.append(keyed)

    def wait_for_transmit_clear(self, *, clear_seconds: float, poll_seconds: float, deadline: datetime, now: Callable[[], datetime], sleep_fn: Callable[[float], None], fail_fast_on_busy: bool = False) -> bool:
        _ = (poll_seconds, deadline, now, sleep_fn, fail_fast_on_busy)
        self.wait_calls += 1
        if clear_seconds <= 0:
            return not self.channel_busy()
        # Instant clear for tests (sequence already encodes busy at check points).
        return True


def test_busy_after_synthesize_defers_without_ptt() -> None:
    radio = GatingRadio([False, True])  # start idle, busy after synth
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    deps.dra_clear_seconds = 0
    assert handle_fire(make_ctx(), deps) == "deferred"
    assert extras["played"] == []
    assert radio.ptt_events == []


def test_clear_wait_runs_for_dra_backend() -> None:
    radio = GatingRadio([False, False, False, False])
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    deps.dra_clear_seconds = 10.0
    assert handle_fire(make_ctx(), deps) == "transmitted"
    assert radio.wait_calls == 1
    assert extras["played"] == ["play"]


def test_transmit_aborts_when_busy_at_key(caplog: pytest.LogCaptureFixture) -> None:
    class BusyAtKey:
        def channel_busy(self) -> bool:
            return True

        def set_ptt(self, keyed: bool) -> None:
            _ = keyed
            raise AssertionError("should not key")

    def play(_chunks: Iterable[PcmChunk]) -> None:
        return

    with caplog.at_level("WARNING"):
        try:
            transmit(
                [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
                radio=BusyAtKey(),
                play_fn=play,
            )
        except ChannelBusyError:
            pass
        else:
            raise AssertionError("expected ChannelBusyError")
