"""Gate API traffic until background startup (Piper preload, etc.) finishes."""

from __future__ import annotations

import asyncio

_ready = asyncio.Event()


def clear_startup_ready() -> None:
    _ready.clear()


def mark_startup_ready() -> None:
    _ready.set()


def is_startup_ready() -> bool:
    return _ready.is_set()


async def wait_startup_ready() -> None:
    await _ready.wait()
