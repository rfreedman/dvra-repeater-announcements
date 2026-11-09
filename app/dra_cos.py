"""COS sample, idle credit, and clear-wait for a DRA-36M."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Protocol, cast

from app.cm119 import (
    Cm119HidAdapter,
    HidDevice,
    HidModule,
    cos_line_busy,
    read_hid_sample,
)
from app.fire_trace import fire_trace

log = logging.getLogger("app.dra_cos")

OpenHid = Callable[[HidModule, bytes], HidDevice]


class ClearWaitRadio(Protocol):
    """What clear-wait needs from the radio facade (each call takes its own lock)."""

    def channel_busy(self) -> bool: ...

    def continuous_idle_seconds(self) -> float: ...


class CosMonitor:
    """Idle credit and cached COS HID handles. Shares the radio lock.

    Methods named ``*_unlocked`` assume the caller already holds that lock.
    """

    _lock: threading.Lock
    _line: str
    _active_low: bool
    _devices: list[HidDevice | None]
    _idle_since_mono: float | None
    _last_cos_busy: bool | None
    _last_raw_hex: str | None

    def __init__(
        self,
        lock: threading.Lock,
        *,
        line: str,
        active_low: bool,
        device_count: int,
    ) -> None:
        self._lock = lock
        self._line = line
        self._active_low = active_low
        self._devices = [None] * device_count
        self._idle_since_mono = None
        self._last_cos_busy = None
        self._last_raw_hex = None

    def close_devices_unlocked(self) -> None:
        for index, dev in enumerate(self._devices):
            if dev is None:
                continue
            try:
                dev.close()
            except OSError:
                pass
            self._devices[index] = None

    def drop_device_unlocked(self, index: int) -> None:
        dev = self._devices[index]
        if dev is None:
            return
        try:
            dev.close()
        except OSError:
            pass
        self._devices[index] = None

    def ensure_device_unlocked(
        self,
        hid: HidModule,
        index: int,
        adapter: Cm119HidAdapter,
        *,
        single_adapter: bool,
        open_hid: OpenHid,
    ) -> HidDevice:
        dev = self._devices[index]
        if dev is not None:
            return dev
        if single_adapter:
            self.close_devices_unlocked()
        else:
            stale = self._devices[index]
            if stale is not None:
                try:
                    stale.close()
                except OSError:
                    pass
                self._devices[index] = None
        dev = open_hid(hid, adapter.path)
        self._devices[index] = dev
        return dev

    def forget_idle_unlocked(self) -> None:
        self._idle_since_mono = None
        self._last_cos_busy = None

    def _apply_busy_unlocked(self, is_busy: bool) -> None:
        """One decoded busy sample resets idle credit (a kerchunk may be one sample)."""
        mono = time.monotonic()
        if is_busy:
            self._idle_since_mono = None
            self._last_cos_busy = True
        else:
            if self._idle_since_mono is None:
                self._idle_since_mono = mono
            self._last_cos_busy = False

    def _idle_seconds_unlocked(self) -> float:
        if self._idle_since_mono is None:
            return 0.0
        return max(0.0, time.monotonic() - self._idle_since_mono)

    def _duck_cos(self, adapter: object, hid: HidModule) -> bool | None:
        read_tri: object = cast(object, getattr(adapter, "read_cos_tri_state", None))
        if callable(read_tri):
            result = read_tri(hid, line=self._line, active_low=self._active_low)
            return result if isinstance(result, bool) else None
        read_legacy: object = cast(object, getattr(adapter, "read_cos_busy", None))
        if callable(read_legacy):
            result = read_legacy(
                hid,
                line=self._line,
                active_low=self._active_low,
                persistent_dev=None,
            )
            return result if isinstance(result, bool) else None
        return None

    def _sample_one_locked(
        self,
        hid: HidModule,
        adapter: object,
        index: int,
        *,
        ptt_keyed: bool,
        single_adapter: bool,
        open_hid: OpenHid,
    ) -> bool | None:
        if ptt_keyed:
            return None
        if not isinstance(adapter, Cm119HidAdapter):
            return self._duck_cos(adapter, hid)
        try:
            dev = self.ensure_device_unlocked(
                hid,
                index,
                adapter,
                single_adapter=single_adapter,
                open_hid=open_hid,
            )
        except OSError:
            log.debug("COS HID open failed on %s", adapter.label, exc_info=True)
            return None
        sample = read_hid_sample(dev)
        raw = sample.raw
        if raw is None:
            log.debug("COS no HID report on %s; reopening HID handle", adapter.label)
            self.drop_device_unlocked(index)
            try:
                dev = self.ensure_device_unlocked(
                    hid,
                    index,
                    adapter,
                    single_adapter=single_adapter,
                    open_hid=open_hid,
                )
            except OSError:
                log.debug("COS HID reopen failed on %s", adapter.label, exc_info=True)
                return None
            sample = read_hid_sample(dev)
            raw = sample.raw
            if raw is None:
                log.debug("COS no HID report on %s after reopen", adapter.label)
                return None
        self._last_raw_hex = raw.hex()
        busy = cos_line_busy(raw, line=self._line, active_low=self._active_low)
        fire_trace(
            "COS %s raw=%s decoded_busy=%s idle_credit=%.2fs line=%s active_low=%s",
            adapter.label,
            self._last_raw_hex,
            busy,
            self._idle_seconds_unlocked(),
            self._line,
            self._active_low,
        )
        return busy

    def sample_channel_busy_locked(
        self,
        hid: HidModule,
        *,
        ptt_keyed: bool,
        adapters: Sequence[object],
        open_hid: OpenHid,
    ) -> bool:
        saw_busy = False
        saw_idle = False
        single_adapter = len(adapters) == 1
        for index, adapter in enumerate(adapters):
            tri = self._sample_one_locked(
                hid,
                adapter,
                index,
                ptt_keyed=ptt_keyed,
                single_adapter=single_adapter,
                open_hid=open_hid,
            )
            if tri is True:
                saw_busy = True
            elif tri is False:
                saw_idle = True
        if saw_busy:
            self._apply_busy_unlocked(True)
            return True
        if saw_idle:
            self._apply_busy_unlocked(False)
            return False
        if self._last_cos_busy is False and self._idle_since_mono is not None:
            fire_trace(
                "COS sample missed; keeping idle credit %.2fs",
                self._idle_seconds_unlocked(),
            )
            return False
        log.debug("COS indeterminate; treating as busy for gating")
        return True

    def continuous_idle_seconds(self) -> float:
        with self._lock:
            return self._idle_seconds_unlocked()

    def last_cos_busy(self) -> bool | None:
        with self._lock:
            return self._last_cos_busy

    def defer_snapshot(self) -> tuple[bool | None, float]:
        """Last COS state + idle credit without opening HID."""
        with self._lock:
            return self._last_cos_busy, self._idle_seconds_unlocked()

    def set_idle_credit(self, idle_seconds: float) -> None:
        """Pretend COS has been idle for ``idle_seconds`` (tests)."""
        with self._lock:
            self._last_cos_busy = False
            self._idle_since_mono = time.monotonic() - idle_seconds


def wait_for_transmit_clear(
    radio: ClearWaitRadio,
    *,
    clear_seconds: float,
    poll_seconds: float,
    deadline: datetime,
    now: Callable[[], datetime],
    sleep_fn: Callable[[float], None],
    fail_fast_on_busy: bool = False,
) -> bool:
    """Wait for ``clear_seconds`` of continuous COS idle before PTT."""
    from app.dra_alsa_mixer import apply_cm119_replug_recovery

    apply_cm119_replug_recovery(radio)
    if clear_seconds <= 0:
        return not radio.channel_busy()
    poll = max(0.05, poll_seconds)
    clear_remaining = clear_seconds
    clear_ready_at: datetime | None = None

    idle = radio.continuous_idle_seconds()
    if idle >= clear_seconds:
        log.info("COS clear satisfied (%.1fs idle before PTT)", idle)
        return True
    if not radio.channel_busy():
        clear_remaining = clear_seconds - idle

    while True:
        current = now()
        if current >= deadline:
            return False
        if radio.channel_busy():
            if clear_ready_at is not None:
                log.debug("Clear window reset — COS busy again")
            clear_ready_at = None
            clear_remaining = clear_seconds
            if fail_fast_on_busy:
                return False
            remaining = (deadline - current).total_seconds()
            sleep_fn(min(poll, max(0.0, remaining)))
            continue
        if clear_ready_at is None:
            clear_ready_at = current + timedelta(seconds=clear_remaining)
            log.info(
                "COS idle — waiting %.1fs continuous clear before PTT",
                clear_remaining,
            )
        elif current >= clear_ready_at:
            return True
        sleep_until = min(clear_ready_at, deadline)
        remaining = (sleep_until - current).total_seconds()
        sleep_fn(min(poll, max(0.0, remaining)))
