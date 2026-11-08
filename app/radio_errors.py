"""Radio transmit errors (separate module to avoid DRA import cycles)."""

from __future__ import annotations


class ChannelBusyError(Exception):
    """Raised when COS is active immediately before PTT (should not key)."""


class TransmitAbortError(Exception):
    """Playback stopped early (e.g. DRA USB unplugged mid-transmit)."""
