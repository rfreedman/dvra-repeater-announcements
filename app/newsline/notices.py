"""Home notice when a too-long schedule is skipped for another announcement."""

from __future__ import annotations

import threading
from datetime import datetime

_lock = threading.Lock()
_notice: dict[str, object] | None = None


def peek_skip_notice() -> dict[str, object] | None:
    with _lock:
        return dict(_notice) if _notice else None


def clear_skip_notice() -> None:
    global _notice
    with _lock:
        _notice = None


def record_skip_notice(detail: str, *, when: datetime) -> None:
    global _notice
    with _lock:
        _notice = {"detail": detail, "at": when.isoformat()}
