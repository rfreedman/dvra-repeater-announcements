"""Live home status (on-air / manual trigger) shared by REST and SSE."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.manual_trigger_state import pending_manual_triggers, peek_manual_trigger_drop_notice
from app.manual_trigger_types import (
    HomeLiveStatus,
    RunningTransmission,
    SkipNotice,
    UpcomingRow,
    UpcomingRunning,
    UpcomingWaiting,
)
from app.models import StoreDocument
from app.schedule_logic import next_transmission
from app.scheduler import get_running
from app.store import get_store

log = logging.getLogger("app.home_live")


def _skip_notice() -> SkipNotice | None:
    from app.newsline.notices import peek_skip_notice

    return peek_skip_notice()


def upcoming_for_document(
    document: StoreDocument, running: RunningTransmission | None
) -> UpcomingRow | None:
    if running:
        notice = _skip_notice()
        row: UpcomingRunning = {
            "announcement_id": running["announcement_id"],
            "announcement_name": running["announcement_name"],
            "schedule_id": running["schedule_id"],
            "at": running["started_at"],
            "status": "running",
            "phase": running.get("phase") or "playing",
            "clear_seconds": float(running.get("clear_seconds") or 0.0),
            "clear_remaining_seconds": running.get("clear_remaining_seconds"),
            "piece_label": running.get("piece_label"),
            "story_index": running.get("story_index"),
            "story_total": running.get("story_total"),
            "note": notice.get("detail") if notice else None,
        }
        return row
    now = datetime.now(ZoneInfo(document.settings.timezone))
    nxt = next_transmission(document, now)
    if nxt is None or nxt.announcement is None:
        return None
    waiting: UpcomingWaiting = {
        "announcement_id": nxt.announcement.id,
        "announcement_name": nxt.announcement.name,
        "schedule_id": nxt.schedule.id if nxt.schedule else None,
        "schedule_name": nxt.schedule.name if nxt.schedule else None,
        "at": nxt.fire_at.isoformat(),
        "status": "waiting",
        "slot": nxt.slot.stamp,
        "note": nxt.skipped_detail or nxt.warning,
    }
    return waiting


def build_home_live_status() -> HomeLiveStatus:
    from app.newsline.session import bulletin_session

    document = get_store().document()
    now = datetime.now(ZoneInfo(document.settings.timezone))
    return {
        "now": now.isoformat(),
        "upcoming": upcoming_for_document(document, get_running()),
        "pending_manual_triggers": pending_manual_triggers(),
        "manual_trigger_drop_notice": peek_manual_trigger_drop_notice(),
        "skip_notice": _skip_notice(),
        "newsline_playing": bulletin_session.active,
    }
