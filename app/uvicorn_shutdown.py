"""Close SSE streams before uvicorn waits on open connections.

Uvicorn runs lifespan shutdown *after* waiting for HTTP connections to finish.
Long-lived SSE responses never complete until we stop their generators, so
``shutdown_sse_streams()`` must run at the start of ``Server.shutdown()``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.sse_hub import shutdown_sse_streams

if TYPE_CHECKING:
    import socket

    import uvicorn.server

_stats_broadcast_stop: asyncio.Event | None = None
_patched = False


def register_stats_broadcast_stop(stop: asyncio.Event) -> None:
    global _stats_broadcast_stop
    _stats_broadcast_stop = stop


def _stop_stats_broadcast() -> None:
    if _stats_broadcast_stop is not None:
        _stats_broadcast_stop.set()


def _close_sse_before_uvicorn_waits() -> None:
    _stop_stats_broadcast()
    shutdown_sse_streams()


close_sse_before_uvicorn_waits = _close_sse_before_uvicorn_waits


def install_uvicorn_sse_shutdown_hook() -> None:
    global _patched
    if _patched:
        return
    import uvicorn.server

    original = uvicorn.server.Server.shutdown

    async def shutdown(
        self: uvicorn.server.Server, sockets: list[socket.socket] | None = None
    ) -> None:
        _close_sse_before_uvicorn_waits()
        await original(self, sockets=sockets)

    uvicorn.server.Server.shutdown = shutdown  # type: ignore[method-assign]
    _patched = True
