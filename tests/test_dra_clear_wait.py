from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.dra_radio import DraRadio

TZ = ZoneInfo("America/New_York")


class _Adapter:
    def __init__(self, busy_values: list[bool | None]):
        self._values = list(busy_values)

    def read_cos_busy(self, *_args, **_kwargs):
        if not self._values:
            return False
        return self._values.pop(0)


def _radio_with_busy_sequence(values: list[bool | None]) -> DraRadio:
    return DraRadio(
        hid_adapters=[_Adapter(values)],  # type: ignore[list-item]
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )


def test_wait_for_clear_resets_when_busy_returns():
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    clock = {"now": t0}
    sleeps: list[float] = []

    def now():
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


def test_clear_satisfied_when_busy_sample_but_idle_credit():
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    clock = {"now": t0}
    sleeps: list[float] = []

    def now():
        return clock["now"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)

    radio = _radio_with_busy_sequence([True])
    with radio._lock:
        radio._idle_since_mono = time.monotonic() - 12.0
        radio._last_cos_busy = False
    ok = radio.wait_for_transmit_clear(
        clear_seconds=10.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=30),
        now=now,
        sleep_fn=sleep,
    )
    assert ok is True


def test_clear_satisfied_when_idle_before_fire():
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    sleeps: list[float] = []
    radio = _radio_with_busy_sequence([False])
    with radio._lock:
        radio._idle_since_mono = time.monotonic() - 15.0
        radio._last_cos_busy = False
    ok = radio.wait_for_transmit_clear(
        clear_seconds=10.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=30),
        now=lambda: t0,
        sleep_fn=sleeps.append,
    )
    assert ok is True
    assert sleeps == []


def test_partial_idle_credit_shortens_wait():
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    clock = {"now": t0}
    sleeps: list[float] = []

    def now():
        return clock["now"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)

    radio = _radio_with_busy_sequence([False, False, False])
    with radio._lock:
        radio._idle_since_mono = time.monotonic() - 7.0
        radio._last_cos_busy = False
    ok = radio.wait_for_transmit_clear(
        clear_seconds=10.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=30),
        now=now,
        sleep_fn=sleep,
    )
    assert ok is True
    assert sum(sleeps) < 10.0


def test_fail_fast_on_busy_during_clear():
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=TZ)
    sleeps: list[float] = []
    radio = _radio_with_busy_sequence([False, True])
    ok = radio.wait_for_transmit_clear(
        clear_seconds=10.0,
        poll_seconds=1.0,
        deadline=t0 + timedelta(seconds=300),
        now=lambda: t0,
        sleep_fn=sleeps.append,
        fail_fast_on_busy=True,
    )
    assert ok is False
    assert sleeps == []
