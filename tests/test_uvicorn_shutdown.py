"""Uvicorn shutdown hook for SSE (issue #27)."""

from __future__ import annotations

import asyncio

from app.sse_hub import clear_shutdown_state, set_event_loop, shutdown_sse_streams, subscribe, unsubscribe
from app.uvicorn_shutdown import _close_sse_before_uvicorn_waits, install_uvicorn_sse_shutdown_hook


def test_install_uvicorn_sse_shutdown_hook_is_idempotent():
    install_uvicorn_sse_shutdown_hook()
    import uvicorn.server

    first = uvicorn.server.Server.shutdown
    install_uvicorn_sse_shutdown_hook()
    assert uvicorn.server.Server.shutdown is first


def test_close_sse_before_uvicorn_waits_stops_stats_broadcast():
    stop = asyncio.Event()
    from app.uvicorn_shutdown import register_stats_broadcast_stop

    register_stats_broadcast_stop(stop)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        clear_shutdown_state()
        queue = subscribe()
        try:
            _close_sse_before_uvicorn_waits()
            await asyncio.sleep(0)
            assert stop.is_set()
            payload = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert payload is None
        finally:
            unsubscribe(queue)
            clear_shutdown_state()

    asyncio.run(run())
