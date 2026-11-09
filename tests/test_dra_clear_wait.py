from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from app.cm119 import Cm119HidAdapter, HidModule
from app.dra_radio import DraRadio

TZ = ZoneInfo("America/New_York")


class _Adapter:
    _values: list[bool | None]

    def __init__(self, busy_values: list[bool | None]) -> None:
        self._values = list(busy_values)

    def read_cos_busy(
        self,
        _hid: HidModule,
        *,
        line: str,
        active_low: bool,
        persistent_dev: object | None = None,
    ) -> bool | None:
        _ = line
        _ = active_low
        _ = persistent_dev
        if not self._values:
            return False
        return self._values.pop(0)


def _radio_with_busy_sequence(values: list[bool | None]) -> DraRadio:
    return DraRadio(
        hid_adapters=cast(list[Cm119HidAdapter], [_Adapter(values)]),
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )


def test_wait_for_clear_resets_when_busy_returns() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    clock = {"now": t0}
    sleeps: list[float] = []

    def now() -> datetime:
        return clock["now"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)

    # idle, busy (reset), idle, idle long enough
    radio = _radio_with_busy_sequence([False, True, False, False, False])
    ok = radio.wait_for_transmit_clear(
        clear_seconds=2.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=30),
        now=now,
        sleep_fn=sleep,
    )
    assert ok is True
    assert sleeps  # polled at least once


def test_clear_satisfied_when_busy_sample_but_idle_credit() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    clock = {"now": t0}
    sleeps: list[float] = []

    def now() -> datetime:
        return clock["now"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)

    radio = _radio_with_busy_sequence([True])
    radio.set_idle_credit(12.0)
    ok = radio.wait_for_transmit_clear(
        clear_seconds=10.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=30),
        now=now,
        sleep_fn=sleep,
    )
    assert ok is True


def test_clear_satisfied_when_idle_before_fire() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    sleeps: list[float] = []
    radio = _radio_with_busy_sequence([False])
    radio.set_idle_credit(15.0)

    def now() -> datetime:
        return t0

    ok = radio.wait_for_transmit_clear(
        clear_seconds=10.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=30),
        now=now,
        sleep_fn=sleeps.append,
    )
    assert ok is True
    assert sleeps == []


def test_partial_idle_credit_shortens_wait() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    clock = {"now": t0}
    sleeps: list[float] = []

    def now() -> datetime:
        return clock["now"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)

    radio = _radio_with_busy_sequence([False, False, False])
    radio.set_idle_credit(7.0)
    ok = radio.wait_for_transmit_clear(
        clear_seconds=10.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=30),
        now=now,
        sleep_fn=sleep,
    )
    assert ok is True
    assert sum(sleeps) < 10.0


def test_fail_fast_on_busy_during_clear() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    sleeps: list[float] = []
    radio = _radio_with_busy_sequence([False, True])

    def now() -> datetime:
        return t0

    ok = radio.wait_for_transmit_clear(
        clear_seconds=10.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=300),
        now=now,
        sleep_fn=sleeps.append,
        fail_fast_on_busy=True,
    )
    assert ok is False
    assert sleeps == []
