"""Timestamped progress lines for long offline runs (forced align, QA, export).

Writes to stderr (line-buffered / flushed) and optionally to a run log file so
progress is visible even when stdout is captured by a non-TTY agent harness.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.api_types import NewslineProgress

_log_path: Path | None = None
_log_fp = None

_ui_lock = threading.Lock()
_ui_step: str | None = None
_ui_started: float | None = None


def set_log_file(path: Path | None) -> None:
    """Tee all progress lines to ``path`` (append). Pass None to disable."""
    global _log_path, _log_fp
    if _log_fp is not None:
        with contextlib.suppress(OSError):
            _log_fp.close()
        _log_fp = None
    _log_path = path
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_fp = path.open("a", encoding="utf-8")
    # Ensure Python doesn't block-buffer stderr when not a TTY.
    with contextlib.suppress(Exception):
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]


def log_file() -> Path | None:
    return _log_path


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    """Record a progress line on the app logger, stderr, and the run log."""
    import logging

    line = f"[{_stamp()}] {msg}"
    logging.getLogger("app.newsline").info("%s", msg)
    print(line, file=sys.stderr, flush=True)
    if _log_fp is not None:
        _log_fp.write(line + "\n")
        _log_fp.flush()


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``Nm S.Ss`` (minutes and seconds)."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.1f}s"


def begin_ui_progress(step: str) -> None:
    """Start the home-panel timer for a Newsline check."""
    global _ui_step, _ui_started
    with _ui_lock:
        _ui_step = step
        _ui_started = time.perf_counter()


def set_ui_step(step: str) -> None:
    global _ui_step
    with _ui_lock:
        _ui_step = step


def end_ui_progress() -> None:
    global _ui_step, _ui_started
    with _ui_lock:
        _ui_step = None
        _ui_started = None


def ui_snapshot() -> NewslineProgress:
    with _ui_lock:
        step = _ui_step
        started = _ui_started
    elapsed = None if started is None else round(time.perf_counter() - started, 1)
    return {"step": step, "elapsed_seconds": elapsed}


class PhaseTimer:
    """Track elapsed time for a named phase."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.t0 = time.perf_counter()
        set_ui_step(name)
        log(f"{name} …")

    def done(self, detail: str = "") -> float:
        elapsed = time.perf_counter() - self.t0
        suffix = f" — {detail}" if detail else ""
        log(f"{self.name} done in {format_elapsed(elapsed)}{suffix}")
        return elapsed


def progress(current: int, total: int, label: str) -> None:
    """Log ``current/total`` step (1-based current)."""
    set_ui_step(f"[{current}/{total}] {label}")
    log(f"  [{current}/{total}] {label}")
