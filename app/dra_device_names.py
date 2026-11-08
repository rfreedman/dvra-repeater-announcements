"""Shared DRA / CM119 USB audio device name matching (no PortAudio imports)."""

from __future__ import annotations

_DRA_NAME_MARKERS = ("USB PnP Sound Device", "C-Media")


def is_dra_audio_name(name: str) -> bool:
    return any(marker in name for marker in _DRA_NAME_MARKERS)
