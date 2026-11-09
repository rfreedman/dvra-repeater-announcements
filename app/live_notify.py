"""Hooks for pushing live home status to SSE subscribers.

Modules that change on-air state (scheduler, manual triggers, COS poll, Newsline)
notify through here. The server lifespan registers the real publishers on the
process runtime; until then every notify is a no-op. The indirection keeps
those low-level modules from importing the status builder, which would close
an import cycle through ``app.home_live``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.runtime import HomeLiveStatusPublisher, get_runtime

log = logging.getLogger("app.live_notify")


def register_home_live_publishers(
    *,
    status: HomeLiveStatusPublisher,
    data_changed: Callable[[], None],
    throttled: Callable[[], None],
) -> None:
    runtime = get_runtime()
    runtime.status_publisher = status
    runtime.data_changed_publisher = data_changed
    runtime.throttled_publisher = throttled


def notify_home_live_status(*, allow_throttle: bool = False, force: bool = False) -> None:
    """Publish live home status to SSE subscribers (no-op if none connected)."""
    publisher = get_runtime().status_publisher
    if publisher is None:
        return
    try:
        publisher(allow_throttle=allow_throttle, force=force)
    except Exception:
        log.debug("SSE home live notify failed", exc_info=True)


def notify_home_data_changed() -> None:
    """Tell home SSE clients to reload schedules/library (multi-operator edits)."""
    publisher = get_runtime().data_changed_publisher
    if publisher is None:
        return
    try:
        publisher()
    except Exception:
        log.debug("SSE home data_changed notify failed", exc_info=True)


def notify_home_live_status_throttled() -> None:
    """At most once per second; for background COS while manual defer is active."""
    publisher = get_runtime().throttled_publisher
    if publisher is None:
        return
    try:
        publisher()
    except Exception:
        log.debug("SSE home live throttled notify failed", exc_info=True)
