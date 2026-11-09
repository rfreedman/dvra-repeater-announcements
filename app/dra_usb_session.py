"""In-flight DRA USB transmit: abort flag, aplay procs, and playback streams."""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import cast

log = logging.getLogger("app.dra_usb_session")


class UsbTransmitSession:
    """Registry for one transmit window (lead silence through PCM).

    ``begin`` / ``end`` bracket ``transmit()`` on a DRA. USB loss calls ``abort``,
    which sets the flag playback polls and stops any stream or ``aplay`` still running.
    """

    _transmit_abort: threading.Event
    _active: bool
    _streams: list[object]
    _procs: list[subprocess.Popen[bytes]]
    _lock: threading.Lock

    def __init__(self) -> None:
        self._transmit_abort = threading.Event()
        self._active = False
        self._streams = []
        self._procs = []
        self._lock = threading.Lock()

    def begin(self) -> None:
        """Covers USB lead silence + keyed PCM."""
        self._transmit_abort.clear()
        self._active = True

    def end(self) -> None:
        self._active = False
        self._transmit_abort.clear()
        with self._lock:
            self._streams.clear()
            self._procs.clear()

    def transmit_active(self) -> bool:
        return self._active

    def abort_is_set(self) -> bool:
        return self._transmit_abort.is_set()

    def playback_in_progress(self) -> bool:
        with self._lock:
            return bool(self._procs or self._streams)

    def register_stream(self, stream: object) -> None:
        with self._lock:
            self._streams.append(stream)

    def register_aplay_proc(self, proc: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._procs.append(proc)

    def abort(self) -> None:
        """Stop USB audio when the CM119 disappears mid-fire."""
        self._transmit_abort.set()
        self._kill_procs()
        self._close_streams()
        log.warning("DRA USB unplugged during transmit; aborting playback")

    def _kill_procs(self) -> None:
        with self._lock:
            procs = list(self._procs)
            self._procs.clear()
        for proc in procs:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    log.debug("Kill aplay after USB loss", exc_info=True)

    def _close_streams(self) -> None:
        with self._lock:
            streams = list(self._streams)
            self._streams.clear()
        for stream in streams:
            try:
                exit_fn: object = cast(object, getattr(stream, "__exit__", None))
                if callable(exit_fn):
                    _ = exit_fn(None, None, None)
            except Exception:
                log.debug("Closing playback stream after USB loss", exc_info=True)
