"""System stats SSE (issue #27 phase 3)."""

from __future__ import annotations

import asyncio

from app.sse_hub import (
    publish_stats_event,
    set_event_loop,
    subscribe_stats,
    unsubscribe_stats,
)
from app.stats_sse import STATS_BROADCAST_INTERVAL_SECONDS


def test_stats_sse_requires_auth():
    from fastapi.testclient import TestClient

    from app.server import app

    with TestClient(app) as anon:
        res = anon.get("/api/events/stats")
        assert res.status_code == 401


def test_stats_hub_delivers_publish():
    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        queue = subscribe_stats()
        try:
            payload = {
                "celsius": 42.0,
                "thermal_state": None,
                "cpu_percent": 10.0,
                "memory_percent": 20.0,
            }
            publish_stats_event(payload)
            await asyncio.sleep(0)
            message = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert message.event == "stats"
            assert message.data == payload
        finally:
            unsubscribe_stats(queue)

    asyncio.run(run())


def test_stats_sse_stream_yields_snapshot(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.server import _system_stats_event_stream
    from app.sse_hub import format_sse

    sample = {
        "celsius": 50.0,
        "thermal_state": "nominal",
        "cpu_percent": 5.0,
        "memory_percent": 30.0,
    }
    monkeypatch.setattr("app.server.read_system_stats", lambda: sample)

    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=True)

    async def run() -> None:
        stream = _system_stats_event_stream(request)
        first = await anext(stream)
        assert first == format_sse("snapshot", sample)

    asyncio.run(run())


def test_stats_broadcast_interval():
    assert STATS_BROADCAST_INTERVAL_SECONDS == 15.0
