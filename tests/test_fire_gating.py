from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.audio import PcmChunk
from app.fire import handle_fire
from app.radio import ChannelBusyError, transmit

from tests.test_fire import _announcement, _ctx, _deps

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=TZ)


class GatingRadio:
    """Simulates DRA gating: busy_sequence consumed on each channel_busy()."""

    def __init__(self, busy_sequence: list[bool]):
        self._seq = list(busy_sequence)
        self.ptt_events: list[bool] = []
        self.wait_calls = 0

    def channel_busy(self) -> bool:
        if not self._seq:
            return False
        return self._seq.pop(0)

    def set_ptt(self, keyed: bool) -> None:
        self.ptt_events.append(keyed)

    def wait_for_transmit_clear(self, **kwargs) -> bool:
        self.wait_calls += 1
        clear_seconds = kwargs["clear_seconds"]
        if clear_seconds <= 0:
            return not self.channel_busy()
        # Instant clear for tests (sequence already encodes busy at check points).
        return True


def test_busy_after_synthesize_defers_without_ptt():
    radio = GatingRadio([False, True])  # start idle, busy after synth
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)  # type: ignore[arg-type]
    deps.dra_clear_seconds = 0
    assert handle_fire(_ctx(), deps) == "deferred"
    assert extras["played"] == []
    assert radio.ptt_events == []


def test_clear_wait_runs_for_dra_backend():
    radio = GatingRadio([False, False, False, False])
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)  # type: ignore[arg-type]
    deps.dra_clear_seconds = 10.0
    assert handle_fire(_ctx(), deps) == "transmitted"
    assert radio.wait_calls == 1
    assert extras["played"] == ["play"]


def test_transmit_aborts_when_busy_at_key(caplog):
    class BusyAtKey:
        def channel_busy(self) -> bool:
            return True

        def set_ptt(self, _keyed: bool) -> None:
            raise AssertionError("should not key")

    with caplog.at_level("WARNING"):
        try:
            transmit(
                [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
                radio=BusyAtKey(),  # type: ignore[arg-type]
                play_fn=lambda _c: None,
            )
        except ChannelBusyError:
            pass
        else:
            raise AssertionError("expected ChannelBusyError")
