"""In-process SSE fan-out for home live status and system stats."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

from app.home_live import build_home_live_status
from app.live_notify import register_home_live_publishers
from app.manual_trigger_types import SystemStats

log = logging.getLogger("app.sse_hub")

THROTTLED_PUBLISH_INTERVAL_SECONDS = 1.0

_loop: asyncio.AbstractEventLoop | None = None
_subscribers: set[asyncio.Queue[SseHomeMessage | None]] = set()
_stats_subscribers: set[asyncio.Queue[SseHomeMessage | None]] = set()
_subscribers_lock = threading.Lock()
_shutting_down = False
_publish_lock = threading.Lock()
_last_publish_mono = 0.0
_throttle_lock = threading.Lock()
_last_throttled_publish_mono = 0.0
MIN_PUBLISH_INTERVAL_SECONDS = 0.05

# JSON object payloads (TypedDicts are Mapping[str, object] at runtime).
SsePayload = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SseHomeMessage:
    event: str
    data: SsePayload


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def clear_shutdown_state() -> None:
    global _shutting_down
    _shutting_down = False


def sse_shutting_down() -> bool:
    return _shutting_down


def shutdown_sse_streams() -> None:
    """Unblock all SSE stream handlers so uvicorn can exit (open browser tabs)."""
    global _shutting_down
    _shutting_down = True
    with _subscribers_lock:
        targets = list(_subscribers) + list(_stats_subscribers)
    on_registered_loop = False
    try:
        running = asyncio.get_running_loop()
        on_registered_loop = running is _loop
    except RuntimeError:
        pass
    for queue in targets:
        if on_registered_loop:
            _close_queue(queue)
        elif _loop is not None:
            try:
                _ = _loop.call_soon_threadsafe(_close_queue, queue)
            except RuntimeError:
                _close_queue(queue)
        else:
            _close_queue(queue)


def subscribe() -> asyncio.Queue[SseHomeMessage | None]:
    queue: asyncio.Queue[SseHomeMessage | None] = asyncio.Queue(maxsize=16)
    with _subscribers_lock:
        _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue[SseHomeMessage | None]) -> None:
    with _subscribers_lock:
        _subscribers.discard(queue)


def subscribe_stats() -> asyncio.Queue[SseHomeMessage | None]:
    queue: asyncio.Queue[SseHomeMessage | None] = asyncio.Queue(maxsize=8)
    with _subscribers_lock:
        _stats_subscribers.add(queue)
    return queue


def unsubscribe_stats(queue: asyncio.Queue[SseHomeMessage | None]) -> None:
    with _subscribers_lock:
        _stats_subscribers.discard(queue)


def _has_subscribers() -> bool:
    with _subscribers_lock:
        return bool(_subscribers)


def has_stats_subscribers() -> bool:
    with _subscribers_lock:
        return bool(_stats_subscribers)


def publish_sse_event(event: str, data: SsePayload, *, force: bool = False) -> None:
    if not _has_subscribers():
        return
    loop = _loop
    if loop is None:
        log.debug("SSE publish skipped: event loop not registered")
        return

    global _last_publish_mono
    if not force:
        now = time.monotonic()
        with _publish_lock:
            if now - _last_publish_mono < MIN_PUBLISH_INTERVAL_SECONDS:
                return
            _last_publish_mono = now
    else:
        with _publish_lock:
            _last_publish_mono = time.monotonic()

    message = SseHomeMessage(event=event, data=data)
    with _subscribers_lock:
        targets = list(_subscribers)
    for queue in targets:
        try:
            _ = loop.call_soon_threadsafe(_enqueue, queue, message)
        except RuntimeError:
            log.debug("SSE publish skipped: event loop not running", exc_info=True)


def publish_home_live_status(*, allow_throttle: bool = False, force: bool = False) -> None:
    if allow_throttle:
        now = time.monotonic()
        with _publish_lock:
            if now - _last_publish_mono < 1.0:
                return
    try:
        payload = build_home_live_status()
    except Exception:
        log.warning("Could not build home live status for SSE", exc_info=True)
        return
    publish_sse_event("status", payload, force=force)


def publish_home_live_status_throttled() -> None:
    """At most once per second; for background COS while manual defer is active."""
    global _last_throttled_publish_mono
    now = time.monotonic()
    with _throttle_lock:
        if now - _last_throttled_publish_mono < THROTTLED_PUBLISH_INTERVAL_SECONDS:
            return
        _last_throttled_publish_mono = now
    live = build_home_live_status()
    if not live.get("upcoming") and not live.get("pending_manual_triggers"):
        return
    publish_home_live_status(allow_throttle=False)


def publish_home_data_changed() -> None:
    publish_sse_event("data_changed", {}, force=True)


def publish_stats_event(data: SystemStats) -> None:
    if not has_stats_subscribers():
        return
    loop = _loop
    if loop is None:
        log.debug("Stats SSE publish skipped: event loop not registered")
        return
    message = SseHomeMessage(event="stats", data=data)
    with _subscribers_lock:
        targets = list(_stats_subscribers)
    for queue in targets:
        try:
            _ = loop.call_soon_threadsafe(_enqueue, queue, message)
        except RuntimeError:
            log.debug("Stats SSE publish skipped: event loop not running", exc_info=True)


def _close_queue(queue: asyncio.Queue[SseHomeMessage | None]) -> None:
    try:
        queue.put_nowait(None)
    except asyncio.QueueFull:
        try:
            while True:
                _ = queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


def _enqueue(queue: asyncio.Queue[SseHomeMessage | None], message: SseHomeMessage) -> None:
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        try:
            _ = queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


def format_sse(event: str, data: SsePayload) -> str:
    body = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n"


register_home_live_publishers(
    status=publish_home_live_status,
    data_changed=publish_home_data_changed,
    throttled=publish_home_live_status_throttled,
)
