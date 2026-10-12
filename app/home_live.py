"""Live home status (on-air / manual trigger) shared by REST and SSE."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.models import StoreDocument
from app.schedule_logic import next_transmission
from app.scheduler import (
    get_running,
    pending_manual_triggers,
    peek_manual_trigger_drop_notice,
)
from app.store import get_store

log = logging.getLogger("app.home_live")

_throttle_lock = threading.Lock()
_last_throttled_publish_mono = 0.0
THROTTLED_PUBLISH_INTERVAL_SECONDS = 1.0


def upcoming_for_document(
    document: StoreDocument, running: dict[str, object] | None
) -> dict[str, object] | None:
    if running:
        return {
            "announcement_id": running.get("announcement_id"),
            "announcement_name": running.get("announcement_name"),
            "schedule_id": running.get("schedule_id"),
            "at": running.get("started_at"),
            "status": "running",
        }
    now = datetime.now(ZoneInfo(document.settings.timezone))
    nxt = next_transmission(document, now)
    if nxt is None or nxt.announcement is None:
        return None
    return {
        "announcement_id": nxt.announcement.id,
        "announcement_name": nxt.announcement.name,
        "schedule_id": nxt.schedule.id if nxt.schedule else None,
        "schedule_name": nxt.schedule.name if nxt.schedule else None,
        "at": nxt.fire_at.isoformat(),
        "status": "waiting",
        "slot": nxt.slot.stamp,
    }


def build_home_live_status() -> dict[str, Any]:
    document = get_store().document()
    now = datetime.now(ZoneInfo(document.settings.timezone))
    return {
        "now": now.isoformat(),
        "upcoming": upcoming_for_document(document, get_running()),
        "pending_manual_triggers": pending_manual_triggers(),
        "manual_trigger_drop_notice": peek_manual_trigger_drop_notice(),
    }


def notify_home_live_status(*, allow_throttle: bool = False, force: bool = False) -> None:
    """Publish live home status to SSE subscribers (no-op if none connected)."""
    try:
        from app.sse_hub import publish_home_live_status

        publish_home_live_status(allow_throttle=allow_throttle, force=force)
    except Exception:
        log.debug("SSE home live notify failed", exc_info=True)


def notify_home_live_status_throttled() -> None:
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
    notify_home_live_status(allow_throttle=False)
