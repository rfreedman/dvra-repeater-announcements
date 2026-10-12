"""In-process SSE fan-out for home live status."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from app.home_live import build_home_live_status

log = logging.getLogger("app.sse_hub")

_loop: asyncio.AbstractEventLoop | None = None
_subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
_subscribers_lock = threading.Lock()
_publish_lock = threading.Lock()
_last_publish_mono = 0.0
MIN_PUBLISH_INTERVAL_SECONDS = 0.05


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue[dict[str, Any] | None]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=16)
    with _subscribers_lock:
        _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue[dict[str, Any] | None]) -> None:
    with _subscribers_lock:
        _subscribers.discard(queue)


def _has_subscribers() -> bool:
    with _subscribers_lock:
        return bool(_subscribers)


def publish_home_live_status(*, allow_throttle: bool = False, force: bool = False) -> None:
    if not _has_subscribers():
        return
    loop = _loop
    if loop is None or not loop.is_running():
        return
    import time

    global _last_publish_mono
    if not force:
        if allow_throttle:
            now = time.monotonic()
            with _publish_lock:
                if now - _last_publish_mono < 1.0:
                    return
                _last_publish_mono = now
        else:
            with _publish_lock:
                now = time.monotonic()
                if now - _last_publish_mono < MIN_PUBLISH_INTERVAL_SECONDS:
                    return
                _last_publish_mono = now
    else:
        with _publish_lock:
            _last_publish_mono = time.monotonic()
    try:
        payload = build_home_live_status()
    except Exception:
        log.warning("Could not build home live status for SSE", exc_info=True)
        return
    with _subscribers_lock:
        targets = list(_subscribers)
    for queue in targets:
        try:
            loop.call_soon_threadsafe(_enqueue, queue, payload)
        except RuntimeError:
            pass


def _enqueue(queue: asyncio.Queue[dict[str, Any] | None], payload: dict[str, Any]) -> None:
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def format_sse(event: str, data: dict[str, Any]) -> str:
    body = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n"
