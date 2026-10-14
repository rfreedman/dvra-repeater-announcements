"""DRA-36M / CM119A radio backend for scheduled transmit (PTT + COS + USB audio)."""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from app.audio import PcmChunk
from app.cm119 import (
    Cm119HidAdapter,
    cos_line_busy,
    ensure_hid_module,
    list_cm119_hid_entries,
    open_cm119_hid_at,
    read_hid_sample,
    set_gpio3_ptt,
)
from app.fire_trace import fire_trace
from app.config import (
    DRA_AUDIO_DEVICE_SPECS,
    DRA_COS_ACTIVE_HIGH,
    DRA_COS_LINE,
    DRA_HID_PATH_BYTES,
    DRA_USB_CHANNEL,
)
from app.dra_playback import (
    play_pcm_chunks_to_dra_devices,
    refresh_portaudio_device_list,
    resolve_dra_audio_indices,
)
from app.radio import ChannelBusyError

log = logging.getLogger("app.dra_radio")


def dra_radio_from(radio: object) -> DraRadio | None:
    """Unwrap ``_RunningOnKeyRadio`` (and similar) for DRA-only helpers."""
    candidate = getattr(radio, "_inner", radio)
    return candidate if isinstance(candidate, DraRadio) else None


class DraRadio:
    """One or two DRA-36M units: OR busy, parallel PTT, mirrored USB audio."""

    def __init__(
        self,
        *,
        hid_adapters: list[Cm119HidAdapter],
        audio_indices: list[int],
        usb_channel: str,
        cos_line: str,
        cos_active_high: bool,
    ) -> None:
        if not hid_adapters:
            raise ValueError("At least one CM119 HID adapter is required")
        if not audio_indices:
            raise ValueError("At least one DRA audio device is required")
        self._hid_adapters = hid_adapters
        self._audio_indices = audio_indices
        self._usb_channel = usb_channel
        self._cos_line = cos_line
        self._cos_active_low = not cos_active_high
        self._lock = threading.Lock()
        self._ptt_devices: list[object] = []
        self._ptt_keyed = False
        self._idle_since_mono: float | None = None
        self._last_cos_busy: bool | None = None
        self._cos_devices: list[object | None] = [None] * len(hid_adapters)
        self._last_cos_raw_hex: str | None = None
        self._usb_hid_absent = False
        self._usb_hid_reconnected_pending = False
        self._pending_audio_indices: list[int] | None = None
        self._transmit_abort = threading.Event()
        self._active_playback_streams: list[object] = []
        self._active_aplay_procs: list[subprocess.Popen[bytes]] = []
        self._play_streams_lock = threading.Lock()

    @property
    def ptt_keyed(self) -> bool:
        with self._lock:
            return self._ptt_keyed

    @property
    def usb_hid_present(self) -> bool:
        return not self._usb_hid_absent

    def _runtime_usb_hid_tracking_enabled(self) -> bool:
        """Linux NUC hot-unplug/replug; off for tests with fake adapters and non-Linux dev."""
        if not sys.platform.startswith("linux"):
            return False
        return all(isinstance(adapter, Cm119HidAdapter) for adapter in self._hid_adapters)

    @property
    def hid_count(self) -> int:
        return len(self._hid_adapters)

    @property
    def audio_device_indices(self) -> list[int]:
        return list(self._audio_indices)

    @classmethod
    def from_config(cls) -> DraRadio:
        hid = ensure_hid_module()
        entries = list_cm119_hid_entries(hid)
        if not entries:
            raise RuntimeError(
                "No CM119A HID interface found. Is the DRA-36M plugged in and accessible?"
                "\nIf attempting to test the system without the DRA-36M and radio, change TTS_RADIO_BACKEND in the .env file to \"stub\" instead of \"dra\""

            )
        if DRA_HID_PATH_BYTES is not None:
            entries = [e for e in entries if e.get("path") == DRA_HID_PATH_BYTES]
            if not entries:
                paths = [e.get("path", b"").hex() for e in list_cm119_hid_entries(hid)]
                raise RuntimeError(
                    "TTS_DRA_HID_PATH did not match any CM119 HID path. "
                    f"Available paths (hex): {paths}. "
                    "Run: python scripts/dra36m_check.py probe"
                )
            log.info("DRA HID pinned via TTS_DRA_HID_PATH (%s)", DRA_HID_PATH_BYTES.hex())

        adapters = [
            Cm119HidAdapter(path=entry["path"], label=f"dra-hid-{i + 1}")
            for i, entry in enumerate(entries)
        ]
        audio_indices = resolve_dra_audio_indices(device_specs=DRA_AUDIO_DEVICE_SPECS)
        n_hid = len(adapters)
        n_audio = len(audio_indices)
        count = min(n_hid, n_audio)

        if n_hid == n_audio == 1:
            log.info(
                "Single DRA paired: HID %s → PortAudio index %s",
                adapters[0].path.hex(),
                audio_indices[0],
            )
        elif n_hid == n_audio:
            log.info(
                "DRA paired on one host: %s CM119 HID → %s PortAudio device(s)",
                n_hid,
                n_audio,
            )
            for i in range(n_hid):
                log.info(
                    "  dra-%s HID %s → PortAudio index %s",
                    i + 1,
                    adapters[i].path.hex(),
                    audio_indices[i],
                )
        else:
            log.warning(
                "DRA HID count (%s) != PortAudio device count (%s); using %s pair(s). "
                "Two radios on one NUC need two CM119 HID interfaces "
                "(python scripts/dra36m_check.py probe). Unset TTS_DRA_HID_PATH when both "
                "DRAs are connected; check udev/plugdev (docs/final-hardware/README.md). "
                "Optional: TTS_DRA_AUDIO_DEVICES and fixed USB ports.",
                n_hid,
                n_audio,
                count,
            )
        return cls(
            hid_adapters=adapters[:count],
            audio_indices=audio_indices[:count],
            usb_channel=DRA_USB_CHANNEL,
            cos_line=DRA_COS_LINE,
            cos_active_high=DRA_COS_ACTIVE_HIGH,
        )

    def _apply_cos_busy(self, is_busy: bool) -> None:
        """Update idle credit; one decoded busy sample resets it (a kerchunk may be one sample)."""
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

    def _close_cos_devices_locked(self) -> None:
        for index, dev in enumerate(self._cos_devices):
            if dev is None:
                continue
            try:
                dev.close()
            except OSError:
                pass
            self._cos_devices[index] = None

    def _drop_cos_device_locked(self, index: int) -> None:
        dev = self._cos_devices[index]
        if dev is None:
            return
        try:
            dev.close()
        except OSError:
            pass
        self._cos_devices[index] = None

    def _enumerate_hid_entries(self) -> list[dict]:
        hid = ensure_hid_module()
        entries = list_cm119_hid_entries(hid)
        if DRA_HID_PATH_BYTES is not None:
            pinned = [e for e in entries if e.get("path") == DRA_HID_PATH_BYTES]
            if pinned:
                return pinned
            # USB replug assigns a new HID path; stale TTS_DRA_HID_PATH must not block recovery.
            if len(self._hid_adapters) == 1 and len(entries) == 1:
                log.warning(
                    "TTS_DRA_HID_PATH (%s) stale after USB replug; using %s",
                    DRA_HID_PATH_BYTES.hex(),
                    entries[0]["path"].hex(),
                )
                return entries
            if len(self._hid_adapters) == 1 and entries:
                log.warning(
                    "TTS_DRA_HID_PATH (%s) stale; using first CM119 HID %s",
                    DRA_HID_PATH_BYTES.hex(),
                    entries[0]["path"].hex(),
                )
                return entries[:1]
            return []
        return entries

    def _register_playback_stream(self, stream: object) -> None:
        with self._play_streams_lock:
            self._active_playback_streams.append(stream)

    def _register_aplay_proc(self, proc: subprocess.Popen[bytes]) -> None:
        with self._play_streams_lock:
            self._active_aplay_procs.append(proc)

    def _kill_active_aplay_procs(self) -> None:
        with self._play_streams_lock:
            procs = list(self._active_aplay_procs)
            self._active_aplay_procs.clear()
        for proc in procs:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    log.debug("Kill aplay after USB loss", exc_info=True)

    def _close_active_playback_streams(self) -> None:
        with self._play_streams_lock:
            streams = list(self._active_playback_streams)
            self._active_playback_streams.clear()
        for stream in streams:
            try:
                stream.__exit__(None, None, None)  # type: ignore[attr-defined]
            except Exception:
                log.debug("Closing playback stream after USB loss", exc_info=True)

    def abort_transmit_due_to_usb_loss(self) -> None:
        """Stop USB audio when the CM119 disappears mid-fire (PTT cleared in software only)."""
        self._transmit_abort.set()
        self._kill_active_aplay_procs()
        self._close_active_playback_streams()
        log.warning("DRA USB unplugged during transmit; aborting playback")

    def _enter_usb_hid_absent_state(self) -> None:
        abort_playback = False
        with self._lock:
            newly_absent = not self._usb_hid_absent
            abort_playback = newly_absent and self._ptt_keyed
            if self._ptt_keyed:
                log.warning("DRA USB unplugged while PTT keyed; closing COS handles")
            self._close_cos_devices_locked()
            # USB is gone — do not write PTT to stale HID handles; clear software state.
            self._ptt_devices = []
            self._ptt_keyed = False
            self._idle_since_mono = None
            self._last_cos_busy = None
            self._usb_hid_absent = True
        if newly_absent:
            log.warning(
                "DRA CM119 HID not found (USB unplugged?) — COS/PTT idle until reconnect"
            )
        if abort_playback:
            self.abort_transmit_due_to_usb_loss()

    def note_usb_hid_presence(self) -> bool:
        """True when enough CM119 HID interfaces exist for this radio configuration."""
        if not self._runtime_usb_hid_tracking_enabled():
            return True
        try:
            entries = self._enumerate_hid_entries()
        except OSError:
            log.debug("CM119 HID enumerate failed", exc_info=True)
            entries = []
        count = len(self._hid_adapters)
        if not entries or len(entries) < count:
            self._enter_usb_hid_absent_state()
            return False
        if self._usb_hid_absent:
            log.info("DRA CM119 HID available again")
            self._usb_hid_absent = False
            self._usb_hid_reconnected_pending = True
        return True

    def clear_usb_hid_reconnected_pending(self) -> None:
        self._usb_hid_reconnected_pending = False

    @property
    def usb_hid_reconnected_pending(self) -> bool:
        return self._usb_hid_reconnected_pending

    def sync_audio_output_indices_if_changed(self, *, after_usb_event: bool = False) -> bool:
        """Refresh PortAudio output indices after USB replug (hw index often changes)."""
        if after_usb_event:
            refresh_portaudio_device_list()
        try:
            new_indices = resolve_dra_audio_indices(device_specs=DRA_AUDIO_DEVICE_SPECS)
        except RuntimeError:
            log.debug("DRA PortAudio re-resolve failed after USB replug", exc_info=True)
            return False
        if new_indices == self._audio_indices:
            if after_usb_event:
                log.info(
                    "DRA USB replug: PortAudio re-scanned; output indices unchanged %s",
                    new_indices,
                )
                return True
            return False
        with self._lock:
            if self._ptt_keyed:
                self._pending_audio_indices = list(new_indices)
                log.debug(
                    "Deferred PortAudio index update %s → %s until PTT unkey",
                    self._audio_indices,
                    new_indices,
                )
                return False
            from app.dra_playback import portaudio_index_is_dra_output

            if not all(portaudio_index_is_dra_output(index) for index in new_indices):
                log.warning(
                    "DRA USB replug: refusing PortAudio indices %s (not DRA outputs)",
                    new_indices,
                )
                return False
            old = list(self._audio_indices)
            self._audio_indices = new_indices
            self._pending_audio_indices = None
        log.info(
            "DRA USB replug: PortAudio output indices updated %s → %s",
            old,
            new_indices,
        )
        return True

    def _apply_pending_audio_indices_unlocked(self) -> None:
        pending = self._pending_audio_indices
        if not pending or pending == self._audio_indices:
            self._pending_audio_indices = None
            return
        old = list(self._audio_indices)
        self._audio_indices = pending
        self._pending_audio_indices = None
        log.info(
            "DRA USB replug: applied deferred PortAudio indices %s → %s",
            old,
            self._audio_indices,
        )

    def audio_output_indices_look_valid(self) -> bool:
        from app.dra_playback import portaudio_index_is_dra_output

        return all(portaudio_index_is_dra_output(index) for index in self._audio_indices)

    def sync_hid_adapters_if_replugged(self) -> bool:
        """Refresh CM119 HID paths after USB replug (ALSA card number may be unchanged)."""
        if not self.note_usb_hid_presence():
            return False
        try:
            entries = self._enumerate_hid_entries()
        except OSError:
            log.debug("CM119 HID enumerate failed during replug check", exc_info=True)
            return False
        count = len(self._hid_adapters)
        assert entries and len(entries) >= count
        entries = entries[:count]
        new_paths = tuple(entry["path"] for entry in entries)
        old_paths = tuple(adapter.path for adapter in self._hid_adapters)
        if new_paths == old_paths:
            return False
        with self._lock:
            if self._ptt_keyed:
                log.warning("USB replug HID path update skipped while PTT is keyed")
                return False
            self._hid_adapters = [
                Cm119HidAdapter(path=entries[i]["path"], label=self._hid_adapters[i].label)
                for i in range(count)
            ]
            self._close_cos_devices_locked()
            self._ptt_devices = []
            self._idle_since_mono = None
            self._last_cos_busy = None
        log.info(
            "DRA USB replug: CM119 HID paths updated (%s → %s)",
            ", ".join(p.hex() for p in old_paths),
            ", ".join(p.hex() for p in new_paths),
        )
        return True

    def reset_hid_after_usb_replug(self) -> None:
        """Drop cached HID handles and idle credit after USB disconnect/reconnect."""
        with self._lock:
            if self._ptt_keyed:
                log.warning("USB replug HID reset skipped while PTT is keyed")
                return
            self._close_cos_devices_locked()
            self._ptt_devices = []
            self._idle_since_mono = None
            self._last_cos_busy = None
            log.info("DRA USB replug: closed cached CM119 HID handles and cleared COS idle credit")

    def _open_hid_path(self, hid, path: bytes):
        """Open CM119 HID; macOS often needs a brief pause after a prior close on the same path."""
        last: OSError | None = None
        for delay in (0.0, 0.03, 0.08, 0.15, 0.3):
            if delay:
                time.sleep(delay)
            try:
                return open_cm119_hid_at(hid, path)
            except OSError as err:
                last = err
        assert last is not None
        raise last

    def _ensure_adapter_hid_locked(self, hid, index: int, adapter: Cm119HidAdapter):
        dev = self._cos_devices[index]
        if dev is not None:
            return dev
        if len(self._hid_adapters) == 1:
            self._close_cos_devices_locked()
        else:
            stale = self._cos_devices[index]
            if stale is not None:
                try:
                    stale.close()
                except OSError:
                    pass
                self._cos_devices[index] = None
        dev = self._open_hid_path(hid, adapter.path)
        self._cos_devices[index] = dev
        return dev

    def warm_open_hid(self) -> None:
        """Open CM119 HID once and keep it (avoids macOS open/close races with PTT)."""
        if not self.note_usb_hid_presence():
            return
        hid = ensure_hid_module()
        for index, adapter in enumerate(self._hid_adapters):
            try:
                with self._lock:
                    self._ensure_adapter_hid_locked(hid, index, adapter)
            except OSError as err:
                if self.sync_hid_adapters_if_replugged():
                    try:
                        with self._lock:
                            self._ensure_adapter_hid_locked(
                                hid,
                                index,
                                self._hid_adapters[index],
                            )
                        continue
                    except OSError:
                        pass
                if not self.note_usb_hid_presence():
                    return
                log.warning(
                    "COS/PTT HID warm-open failed on %s: %s",
                    adapter.label,
                    err,
                )

    def _sample_one_cos_locked(self, hid, adapter: Cm119HidAdapter, index: int) -> bool | None:
        if self._ptt_keyed:
            return None
        if not isinstance(adapter, Cm119HidAdapter):
            read_tri = getattr(adapter, "read_cos_tri_state", None)
            if read_tri is not None:
                return read_tri(
                    hid,
                    line=self._cos_line,
                    active_low=self._cos_active_low,
                )
            read_legacy = getattr(adapter, "read_cos_busy", None)
            if read_legacy is not None:
                return read_legacy(
                    hid,
                    line=self._cos_line,
                    active_low=self._cos_active_low,
                    persistent_dev=None,
                )
            return None
        try:
            dev = self._ensure_adapter_hid_locked(hid, index, adapter)
        except OSError:
            log.debug("COS HID open failed on %s", adapter.label, exc_info=True)
            return None
        sample = read_hid_sample(dev)
        raw = sample.raw
        if raw is None:
            log.debug("COS no HID report on %s; reopening HID handle", adapter.label)
            self._drop_cos_device_locked(index)
            try:
                dev = self._ensure_adapter_hid_locked(hid, index, adapter)
            except OSError:
                log.debug("COS HID reopen failed on %s", adapter.label, exc_info=True)
                return None
            sample = read_hid_sample(dev)
            raw = sample.raw
            if raw is None:
                log.debug("COS no HID report on %s after reopen", adapter.label)
                return None
        self._last_cos_raw_hex = raw.hex()
        busy = cos_line_busy(
            raw,
            line=self._cos_line,
            active_low=self._cos_active_low,
        )
        fire_trace(
            "COS %s raw=%s decoded_busy=%s idle_credit=%.2fs line=%s active_low=%s",
            adapter.label,
            self._last_cos_raw_hex,
            busy,
            self._idle_seconds_unlocked(),
            self._cos_line,
            self._cos_active_low,
        )
        return busy

    def _sample_channel_busy_locked(self, hid) -> bool:
        saw_busy = False
        saw_idle = False
        for index, adapter in enumerate(self._hid_adapters):
            tri = self._sample_one_cos_locked(hid, adapter, index)
            if tri is True:
                saw_busy = True
            elif tri is False:
                saw_idle = True
        if saw_busy:
            self._apply_cos_busy(True)
            return True
        if saw_idle:
            self._apply_cos_busy(False)
            return False
        if self._last_cos_busy is False and self._idle_since_mono is not None:
            fire_trace(
                "COS sample missed; keeping idle credit %.2fs",
                self._idle_seconds_unlocked(),
            )
            return False
        log.debug("COS indeterminate; treating as busy for gating")
        return True

    def refresh_cos(self) -> bool:
        """Read COS once and update idle tracking (background poll)."""
        from app.dra_alsa_mixer import apply_cm119_replug_recovery

        apply_cm119_replug_recovery(self)
        if self._runtime_usb_hid_tracking_enabled() and self._usb_hid_absent:
            return True
        hid = ensure_hid_module()
        with self._lock:
            return self._sample_channel_busy_locked(hid)

    def continuous_idle_seconds(self) -> float:
        with self._lock:
            if self._idle_since_mono is None:
                return 0.0
            return max(0.0, time.monotonic() - self._idle_since_mono)

    @property
    def last_cos_busy(self) -> bool | None:
        with self._lock:
            return self._last_cos_busy

    def cos_defer_snapshot(self) -> tuple[bool | None, float]:
        """Last COS state + idle credit without opening HID (for UI status)."""
        with self._lock:
            idle = 0.0
            if self._idle_since_mono is not None:
                idle = max(0.0, time.monotonic() - self._idle_since_mono)
            return self._last_cos_busy, idle

    def channel_busy(self) -> bool:
        return self.refresh_cos()

    def wait_for_transmit_clear(
        self,
        *,
        clear_seconds: float,
        poll_seconds: float,
        deadline: datetime,
        now: Callable[[], datetime],
        sleep_fn: Callable[[float], None],
        fail_fast_on_busy: bool = False,
    ) -> bool:
        """Wait for ``clear_seconds`` of continuous COS idle before PTT (voice-gate style)."""
        from app.dra_alsa_mixer import apply_cm119_replug_recovery

        apply_cm119_replug_recovery(self)
        if clear_seconds <= 0:
            return not self.channel_busy()
        poll = max(0.05, poll_seconds)
        clear_remaining = clear_seconds
        clear_ready_at: datetime | None = None

        idle = self.continuous_idle_seconds()
        if idle >= clear_seconds:
            log.info("COS clear satisfied (%.1fs idle before PTT)", idle)
            return True
        if not self.channel_busy():
            clear_remaining = clear_seconds - idle

        while True:
            current = now()
            if current >= deadline:
                return False
            if self.channel_busy():
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

    def set_ptt(self, keyed: bool) -> None:
        hid = ensure_hid_module()
        with self._lock:
            if not keyed:
                for dev in self._ptt_devices:
                    try:
                        set_gpio3_ptt(dev, False)
                    except (OSError, ValueError):
                        log.debug("PTT unkey skipped on closed HID handle", exc_info=True)
                self._ptt_devices = []
                self._ptt_keyed = False
                self._apply_pending_audio_indices_unlocked()
                return
            if self._ptt_keyed:
                return
            # Track each radio as soon as it keys so a failure on a later adapter
            # can unkey the ones already transmitting.
            keyed_devices: list[object] = []
            try:
                for index, adapter in enumerate(self._hid_adapters):
                    dev = self._ensure_adapter_hid_locked(hid, index, adapter)
                    fire_trace("PTT key using HID handle on %s", adapter.label)
                    keyed_devices.append(dev)
                    set_gpio3_ptt(dev, True)
            except OSError as err:
                log.warning("PTT HID key failed", exc_info=True)
                for dev in keyed_devices:
                    try:
                        set_gpio3_ptt(dev, False)
                    except OSError:
                        log.error("PTT unkey after failed key also failed", exc_info=True)
                self._ptt_devices = []
                self._ptt_keyed = False
                raise ChannelBusyError("PTT HID unavailable") from err
            self._ptt_devices = keyed_devices
            self._ptt_keyed = True

    def play_transmit_audio(
        self,
        chunks: Iterable[PcmChunk],
        *,
        lead_seconds: float,
    ) -> None:
        """Stream Piper PCM on all DRA outputs (lead silence on USB, not host sleep)."""
        self._transmit_abort.clear()
        try:
            play_pcm_chunks_to_dra_devices(
                chunks,
                device_indices=self._audio_indices,
                channel=self._usb_channel,
                lead_silence=max(0.0, lead_seconds),
                should_abort=self._transmit_abort.is_set,
                register_stream=self._register_playback_stream,
                register_aplay_proc=self._register_aplay_proc,
            )
        finally:
            self._transmit_abort.clear()
            with self._play_streams_lock:
                self._active_playback_streams.clear()
                self._active_aplay_procs.clear()
