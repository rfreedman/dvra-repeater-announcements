"""DRA-36M / CM119A radio backend for scheduled transmit (PTT + COS + USB audio)."""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime

from app.audio import PcmChunk
from app.cm119 import (
    Cm119HidAdapter,
    HidDevice,
    HidDeviceEntry,
    HidModule,
    ensure_hid_module,
    list_cm119_hid_entries,
    open_cm119_hid_at,
    set_gpio3_ptt,
)
from app.config import (
    DRA_AUDIO_DEVICE_SPECS,
    DRA_COS_ACTIVE_HIGH,
    DRA_COS_LINE,
    DRA_HID_PATH_BYTES,
    DRA_USB_CHANNEL,
)
from app.dra_cos import CosMonitor
from app.dra_usb_session import UsbTransmitSession
from app.fire_trace import fire_trace
from app.radio_errors import ChannelBusyError

log = logging.getLogger("app.dra_radio")

# macOS often needs backoff between close/reopen on the same CM119 HID path (bench-tuned).
PTT_HID_OPEN_RETRY_DELAYS_SECONDS = (0.0, 0.03, 0.08, 0.15, 0.3)


class DraRadio:
    """One or two DRA-36M units: OR busy, parallel PTT, mirrored USB audio."""

    _hid_adapters: list[Cm119HidAdapter]
    _audio_indices: list[int]
    _usb_channel: str
    _lock: threading.Lock
    _cos: CosMonitor
    _usb: UsbTransmitSession
    _ptt_devices: list[HidDevice]
    _ptt_keyed: bool
    _usb_hid_absent: bool
    _usb_hid_reconnected_pending: bool
    _pending_audio_indices: list[int] | None

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
        self._lock = threading.Lock()
        self._cos = CosMonitor(
            self._lock,
            line=cos_line,
            active_low=not cos_active_high,
            device_count=len(hid_adapters),
        )
        self._usb = UsbTransmitSession()
        self._ptt_devices = []
        self._ptt_keyed = False
        self._usb_hid_absent = False
        self._usb_hid_reconnected_pending = False
        self._pending_audio_indices = None

    @property
    def ptt_keyed(self) -> bool:
        with self._lock:
            return self._ptt_keyed

    @property
    def usb_hid_present(self) -> bool:
        return not self._usb_hid_absent

    def transmit_abort_is_set(self) -> bool:
        return self._usb.abort_is_set()

    def runtime_usb_hid_tracking_enabled(self) -> bool:
        """Linux NUC hot-unplug/replug; off for tests with fake adapters and non-Linux dev."""
        if not sys.platform.startswith("linux"):
            return False
        # Widen so isinstance stays meaningful for duck-typed test adapters.
        adapters: list[object] = list(self._hid_adapters)
        return all(isinstance(adapter, Cm119HidAdapter) for adapter in adapters)

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
                + "\nIf attempting to test the system without the DRA-36M and radio, "
                + 'change TTS_RADIO_BACKEND in the .env file to "stub" instead of "dra"'
            )
        if DRA_HID_PATH_BYTES is not None:
            entries = [e for e in entries if e.get("path") == DRA_HID_PATH_BYTES]
            if not entries:
                paths = [e.get("path", b"").hex() for e in list_cm119_hid_entries(hid)]
                raise RuntimeError(
                    "TTS_DRA_HID_PATH did not match any CM119 HID path. "
                    + f"Available paths (hex): {paths}. "
                    + "Run: python scripts/dra36m_check.py probe"
                )
            log.info("DRA HID pinned via TTS_DRA_HID_PATH (%s)", DRA_HID_PATH_BYTES.hex())

        from app.dra_portaudio import resolve_dra_audio_indices

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
                + "Two radios on one Linux host need two CM119 HID interfaces "
                + "(python scripts/dra36m_check.py probe). Unset TTS_DRA_HID_PATH when both "
                + "DRAs are connected; check udev/plugdev (docs/final-hardware/README.md). "
                + "Optional: TTS_DRA_AUDIO_DEVICES and fixed USB ports.",
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

    def _enumerate_hid_entries(self) -> list[HidDeviceEntry]:
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

    def begin_usb_transmit(self) -> None:
        """Covers USB lead silence + keyed PCM (``transmit()`` on DRA)."""
        self._usb.begin()

    def end_usb_transmit(self) -> None:
        self._usb.end()

    def abort_transmit_due_to_usb_loss(self) -> None:
        """Stop USB audio when the CM119 disappears mid-fire (PTT cleared in software only)."""
        self._usb.abort()

    def _enter_usb_hid_absent_state(self) -> None:
        abort_playback = False
        playback_active = self._usb.playback_in_progress()
        with self._lock:
            newly_absent = not self._usb_hid_absent
            transmit_active = self._usb.transmit_active()
            abort_playback = newly_absent and (
                self._ptt_keyed or transmit_active or playback_active
            )
            if self._ptt_keyed:
                log.warning("DRA USB unplugged while PTT keyed; closing COS handles")
            elif abort_playback and transmit_active:
                log.warning("DRA USB unplugged during USB audio (pre-PTT lead or playback)")
            self._cos.close_devices_unlocked()
            # USB is gone — do not write PTT to stale HID handles; clear software state.
            self._ptt_devices = []
            self._ptt_keyed = False
            self._cos.forget_idle_unlocked()
            self._usb_hid_absent = True
        if newly_absent:
            log.warning(
                "DRA CM119 HID not found (USB unplugged?) — COS/PTT idle until reconnect"
            )
        if abort_playback:
            self.abort_transmit_due_to_usb_loss()

    def note_usb_hid_presence(self) -> bool:
        """True when enough CM119 HID interfaces exist for this radio configuration."""
        if not self.runtime_usb_hid_tracking_enabled():
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
            log.info(
                "DRA CM119 HID connected again (USB replugged) — COS/PTT recovery starting"
            )
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
        from app.dra_portaudio import refresh_portaudio_device_list, resolve_dra_audio_indices

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
            from app.dra_portaudio import portaudio_index_is_dra_output

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
        from app.dra_portaudio import portaudio_index_is_dra_output

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
            self._cos.close_devices_unlocked()
            self._ptt_devices = []
            self._cos.forget_idle_unlocked()
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
            self._cos.close_devices_unlocked()
            self._ptt_devices = []
            self._cos.forget_idle_unlocked()
            log.info("DRA USB replug: closed cached CM119 HID handles and cleared COS idle credit")

    def _open_hid_path(self, hid: HidModule, path: bytes) -> HidDevice:
        """Open CM119 HID; macOS often needs a brief pause after a prior close on the same path."""
        last: OSError | None = None
        for delay in PTT_HID_OPEN_RETRY_DELAYS_SECONDS:
            if delay:
                time.sleep(delay)
            try:
                return open_cm119_hid_at(hid, path)
            except OSError as err:
                last = err
        assert last is not None
        raise last

    def _ensure_adapter_hid_locked(
        self, hid: HidModule, index: int, adapter: Cm119HidAdapter
    ) -> HidDevice:
        return self._cos.ensure_device_unlocked(
            hid,
            index,
            adapter,
            single_adapter=len(self._hid_adapters) == 1,
            open_hid=self._open_hid_path,
        )

    def warm_open_hid(self) -> None:
        """Open CM119 HID once and keep it (avoids macOS open/close races with PTT)."""
        if not self.note_usb_hid_presence():
            return
        hid = ensure_hid_module()
        for index, adapter in enumerate(self._hid_adapters):
            try:
                with self._lock:
                    _ = self._ensure_adapter_hid_locked(hid, index, adapter)
            except OSError as err:
                if self.sync_hid_adapters_if_replugged():
                    try:
                        with self._lock:
                            _ = self._ensure_adapter_hid_locked(
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

    def refresh_cos(self) -> bool:
        """Read COS once and update idle tracking (background poll)."""
        from app.dra_alsa_mixer import apply_cm119_replug_recovery

        apply_cm119_replug_recovery(self)
        if self.runtime_usb_hid_tracking_enabled() and self._usb_hid_absent:
            return True
        hid = ensure_hid_module()
        with self._lock:
            return self._cos.sample_channel_busy_locked(
                hid,
                ptt_keyed=self._ptt_keyed,
                adapters=self._hid_adapters,
                open_hid=self._open_hid_path,
            )

    def continuous_idle_seconds(self) -> float:
        return self._cos.continuous_idle_seconds()

    @property
    def last_cos_busy(self) -> bool | None:
        return self._cos.last_cos_busy()

    def cos_defer_snapshot(self) -> tuple[bool | None, float]:
        """Last COS state + idle credit without opening HID (for UI status)."""
        return self._cos.defer_snapshot()

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
        from app import dra_cos

        return dra_cos.wait_for_transmit_clear(
            self,
            clear_seconds=clear_seconds,
            poll_seconds=poll_seconds,
            deadline=deadline,
            now=now,
            sleep_fn=sleep_fn,
            fail_fast_on_busy=fail_fast_on_busy,
        )

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
            keyed_devices: list[HidDevice] = []
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

    def prepare_for_transmit(self) -> None:
        """Before PTT: PortAudio re-scan, HID/audio replug recovery, Speaker unmute."""
        from app.dra_alsa_mixer import apply_cm119_replug_recovery, sync_cm119_speaker_unmute

        if not self.runtime_usb_hid_tracking_enabled():
            return
        if self.ptt_keyed:
            return
        reconnected = self.usb_hid_reconnected_pending
        apply_cm119_replug_recovery(self)
        if not self.note_usb_hid_presence():
            return
        pa_rescan = reconnected or not self.audio_output_indices_look_valid()
        _ = self.sync_audio_output_indices_if_changed(after_usb_event=pa_rescan)
        if not self.audio_output_indices_look_valid():
            _ = self.sync_audio_output_indices_if_changed(after_usb_event=True)
        sync_cm119_speaker_unmute(self)
        from typing import cast

        import sounddevice as sd

        for index in self.audio_device_indices:
            try:
                raw = cast(object, sd.query_devices(index))
                name = (
                    str(cast(dict[object, object], raw).get("name", "?"))
                    if isinstance(raw, dict)
                    else "?"
                )
            except Exception:
                name = "?"
            log.info("DRA transmit audio → PortAudio[%s] %s", index, name)

    def play_transmit_audio(
        self,
        chunks: Iterable[PcmChunk],
        *,
        lead_seconds: float,
    ) -> None:
        """Stream Piper PCM on all DRA outputs (lead silence on USB, not host sleep)."""
        from app.dra_playback import play_pcm_chunks_to_dra_devices
        from app.radio_errors import TransmitAbortError

        try:
            play_pcm_chunks_to_dra_devices(
                chunks,
                device_indices=self._audio_indices,
                channel=self._usb_channel,
                lead_silence=max(0.0, lead_seconds),
                should_abort=self._usb.abort_is_set,
                register_stream=self._usb.register_stream,
                register_aplay_proc=self._usb.register_aplay_proc,
            )
        except Exception as exc:
            if isinstance(exc, TransmitAbortError):
                raise
            if self._usb.abort_is_set() or self._usb_hid_absent:
                raise TransmitAbortError("DRA USB playback aborted") from exc
            raise

    def set_idle_credit(self, idle_seconds: float) -> None:
        """Pretend COS has been idle for ``idle_seconds`` (tests)."""
        self._cos.set_idle_credit(idle_seconds)

    def hid_adapter_path(self, index: int = 0) -> bytes:
        return self._hid_adapters[index].path

    def mark_usb_hid_absent(self) -> None:
        self._usb_hid_absent = True

    def mark_ptt_keyed(self, keyed: bool) -> None:
        with self._lock:
            self._ptt_keyed = keyed

    def enumerate_hid_entries(self) -> list[HidDeviceEntry]:
        return self._enumerate_hid_entries()


def as_dra_radio(radio: object) -> DraRadio | None:
    """Narrow to ``DraRadio`` when the backend is concrete DRA."""
    return radio if isinstance(radio, DraRadio) else None





