"""Process composition root for radio, stores, scheduler, and SSE hooks.

Background threads cannot take FastAPI ``Depends``, so the running process
has one ``AppRuntime``. Getters and setters in the service modules read and
write that object. Slots are ``object`` so this module does not import those
services (that import would be a cycle). The server lifespan wires SSE
publishers onto it; importing the service modules does not.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class HomeLiveStatusPublisher(Protocol):
    def __call__(self, *, allow_throttle: bool = False, force: bool = False) -> None: ...


@dataclass
class AppRuntime:
    """Services owned by this process. ``None`` means "not constructed yet"."""

    store: object | None = None
    users: object | None = None
    radio: object | None = None
    registry: object | None = None
    scheduler: object | None = None
    pcm_cache: object | None = None
    manual_trigger_runner: object | None = None
    scheduled_fire_runner: object | None = None
    status_publisher: HomeLiveStatusPublisher | None = None
    data_changed_publisher: Callable[[], None] | None = None
    throttled_publisher: Callable[[], None] | None = None


_runtime: AppRuntime | None = None


def get_runtime() -> AppRuntime:
    """Return this process's services, creating an empty root on first use."""
    global _runtime
    if _runtime is None:
        _runtime = AppRuntime()
    return _runtime
