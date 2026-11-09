"""Operator-facing reasons a schedule will not play as written."""

from __future__ import annotations

from datetime import datetime

from app.models import Schedule, StoreDocument
from app.schedule_play import newsline_blocks_slot
from app.slots import zone_for
from app.spoken_length import blocks_playback, too_long_parts


def offset_allowed(offset_minutes: int, window: int) -> bool:
    return -window <= offset_minutes <= window


def schedule_warning(
    schedule: Schedule,
    document: StoreDocument,
    now: datetime | None = None,
) -> str | None:
    if not schedule.enabled:
        return "Won't play — this schedule is disabled."
    if not (schedule.kind != "baseline" and schedule.is_silence):
        announcement = document.announcement_by_id(schedule.announcement_id)
        if announcement is None:
            return "Won't play — the announcement is missing."
        if newsline_blocks_slot(announcement):
            return (
                "Won't play — Newsline is not available. "
                "The baseline for that slot plays instead, or the slot stays silent if none fits."
            )
        parts = too_long_parts(document, announcement)
        if parts and (announcement.kind == "tts" or blocks_playback(document, announcement)):
            return (
                f"Won't play — {'; '.join(parts)}. "
                "The baseline for that slot plays instead, or the slot stays silent if none fits."
            )
        if parts:
            return f"Plays, but {'; '.join(parts)} will be left out."
        if announcement.kind == "tts" and not announcement.text.strip():
            return "Won't play — announcement text is empty."
    zone = zone_for(document.settings.timezone)
    when = now or datetime.now(zone)
    if (
        schedule.kind == "range"
        and schedule.event_date is not None
        and schedule.event_date < when.date()
    ):
        return "Won't play — the event date has already passed."
    if schedule.kind == "baseline" and not schedule.enabled:
        return "Won't play — this schedule is disabled."
    return None


def document_warnings(document: StoreDocument) -> list[str]:
    warnings: list[str] = []
    if not document.announcements:
        warnings.append("Create at least one announcement, then a baseline schedule that uses it.")
    elif not document.enabled_baselines():
        warnings.append("Add an enabled baseline schedule. Empty slots stay silent until you do.")
    for schedule in document.schedules:
        warning = schedule_warning(schedule, document)
        if warning and ("over the" in warning or "will be left out" in warning):
            warnings.append(f"{schedule.name}: {warning}")
    return warnings
