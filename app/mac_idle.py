"""macOS idle-sleep assertion so the scheduler is not App-Napped."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import POINTER, byref, c_char_p, c_int32, c_uint32, c_void_p

log = logging.getLogger("app.scheduler")

_idle_assertion: c_uint32 | None = None


def _inhibit_idle_sleep() -> None:
    """Ask macOS not to idle-sleep / App Nap this process (no-op elsewhere)."""
    global _idle_assertion
    if sys.platform != "darwin" or _idle_assertion is not None:
        return
    try:
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        k_utf8 = 0x08000100
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        cf.CFStringCreateWithCString.restype = c_void_p
        iokit.IOPMAssertionCreateWithName.argtypes = [
            c_void_p,
            c_uint32,
            c_void_p,
            POINTER(c_uint32),
        ]
        iokit.IOPMAssertionCreateWithName.restype = c_int32
        assertion_id = c_uint32(0)
        status = iokit.IOPMAssertionCreateWithName(
            cf.CFStringCreateWithCString(None, b"PreventUserIdleSystemSleep", k_utf8),
            255,
            cf.CFStringCreateWithCString(None, b"W2ZQ announcement scheduler", k_utf8),
            byref(assertion_id),
        )
        if status != 0:
            log.warning("Idle-sleep assertion failed: %s", status)
            return
        _idle_assertion = assertion_id
        log.info("Inhibited idle sleep so schedule wakes stay on time")
    except Exception:
        log.warning("Could not inhibit idle sleep", exc_info=True)


def _allow_idle_sleep() -> None:
    global _idle_assertion
    if _idle_assertion is None:
        return
    try:
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOPMAssertionRelease.argtypes = [c_uint32]
        iokit.IOPMAssertionRelease.restype = c_int32
        iokit.IOPMAssertionRelease(_idle_assertion)
    except Exception:
        log.debug("Could not release idle-sleep assertion", exc_info=True)
    _idle_assertion = None
