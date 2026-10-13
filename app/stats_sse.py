"""Server-side system stats SSE broadcast (issue #27 phase 3)."""

from __future__ import annotations

import asyncio
import logging

from app.sse_hub import has_stats_subscribers, publish_stats_event
from app.system_status import read_system_stats

log = logging.getLogger("app.stats_sse")

STATS_BROADCAST_INTERVAL_SECONDS = 15.0


async def stats_broadcast_loop(stop: asyncio.Event) -> None:
    """Sample system stats once per interval and fan out to all stats SSE clients."""
    while not stop.is_set():
        if has_stats_subscribers():
            try:
                payload = await asyncio.to_thread(read_system_stats)
                publish_stats_event(payload)
            except Exception:
                log.warning("Stats SSE broadcast failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=STATS_BROADCAST_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            continue
