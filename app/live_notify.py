"""Hooks for pushing live home status to SSE subscribers.

Modules that change on-air state (scheduler, manual triggers, COS poll, Newsline)
notify through here. ``app.sse_hub`` registers the real publishers when it is
imported; without it there are no subscribers and every notify is a no-op.
The indirection keeps those low-level modules from importing the status builder,
which would close an import cycle through ``app.home_live``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger("app.live_notify")


class HomeLiveStatusPublisher(Protocol):
    def __call__(self, *, allow_throttle: bool = False, force: bool = False) -> None: ...


_status_publisher: HomeLiveStatusPublisher | None = None
_data_changed_publisher: Callable[[], None] | None = None
_throttled_publisher: Callable[[], None] | None = None


def register_home_live_publishers(
    *,
    status: HomeLiveStatusPublisher,
    data_changed: Callable[[], None],
    throttled: Callable[[], None],
) -> None:
    global _status_publisher, _data_changed_publisher, _throttled_publisher
    _status_publisher = status
    _data_changed_publisher = data_changed
    _throttled_publisher = throttled


def notify_home_live_status(*, allow_throttle: bool = False, force: bool = False) -> None:
    """Publish live home status to SSE subscribers (no-op if none connected)."""
    publisher = _status_publisher
    if publisher is None:
        return
    try:
        publisher(allow_throttle=allow_throttle, force=force)
    except Exception:
        log.debug("SSE home live notify failed", exc_info=True)


def notify_home_data_changed() -> None:
    """Tell home SSE clients to reload schedules/library (multi-operator edits)."""
    publisher = _data_changed_publisher
    if publisher is None:
        return
    try:
        publisher()
    except Exception:
        log.debug("SSE home data_changed notify failed", exc_info=True)


def notify_home_live_status_throttled() -> None:
    """At most once per second; for background COS while manual defer is active."""
    publisher = _throttled_publisher
    if publisher is None:
        return
    try:
        publisher()
    except Exception:
        log.debug("SSE home live throttled notify failed", exc_info=True)
