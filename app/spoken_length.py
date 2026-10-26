"""Played length of a spoken announcement versus the repeater timeout."""

from __future__ import annotations

from app.audio import PcmChunk
from app.models import Announcement, StoreDocument


def pcm_duration_seconds(chunks: list[PcmChunk]) -> float:
    total = 0.0
    for chunk in chunks:
        width = chunk.sample_width * chunk.channels
        if width <= 0 or chunk.sample_rate <= 0:
            continue
        total += len(chunk.pcm_int16) / (width * chunk.sample_rate)
    return total


def speech_limit_seconds(timeout_seconds: int) -> float:
    return float(timeout_seconds) - 2.0


def measure_announcement(announcement: Announcement, timeout_seconds: int) -> Announcement:
    if announcement.kind != "tts":
        return announcement
    from app.pcm_cache import chunks_for_announcement

    chunks = chunks_for_announcement(announcement)
    return apply_played_length(announcement, pcm_duration_seconds(chunks), timeout_seconds)


def apply_played_length(
    announcement: Announcement,
    play_seconds: float,
    timeout_seconds: int,
) -> Announcement:
    limit = speech_limit_seconds(timeout_seconds)
    return announcement.model_copy(
        update={
            "play_seconds": play_seconds,
            "too_long": play_seconds > limit,
        }
    )


def recompute_too_long(announcement: Announcement, timeout_seconds: int) -> Announcement:
    if announcement.kind != "tts" or announcement.play_seconds is None:
        return announcement
    too_long = announcement.play_seconds > speech_limit_seconds(timeout_seconds)
    if too_long == announcement.too_long:
        return announcement
    return announcement.model_copy(update={"too_long": too_long})


def format_seconds(value: float) -> str:
    return f"{value:.1f}"


def _role_label(role: str) -> str:
    return {"opening": "Opening", "station ID": "Station ID", "closing": "Closing"}[role]


def too_long_parts(document: StoreDocument, announcement: Announcement) -> list[str]:
    """Human descriptions of spoken pieces that exceed the repeater limit."""
    limit = speech_limit_seconds(document.settings.repeater_timeout_seconds)
    limit_text = format_seconds(limit)
    parts: list[str] = []
    if announcement.kind == "tts" and announcement.too_long and announcement.play_seconds is not None:
        parts.append(
            f"{announcement.name} is {format_seconds(announcement.play_seconds)}s, "
            f"over the {limit_text}s limit"
        )
    if announcement.kind != "newsline":
        return parts
    refs = (
        ("opening", announcement.opening_announcement_id),
        ("station ID", announcement.station_id_announcement_id),
        ("closing", announcement.closing_announcement_id),
    )
    for role, announcement_id in refs:
        if not announcement_id:
            continue
        ref = document.announcement_by_id(announcement_id)
        if ref is None or not ref.too_long or ref.play_seconds is None:
            continue
        parts.append(
            f"{_role_label(role)} “{ref.name}” is {format_seconds(ref.play_seconds)}s, "
            f"over the {limit_text}s limit"
        )
    return parts


def blocks_playback(document: StoreDocument, announcement: Announcement | None) -> bool:
    """True when this library item must not go on the air as the slot's announcement."""
    if announcement is None:
        return True
    if announcement.kind == "tts":
        return announcement.too_long
    station = document.announcement_by_id(announcement.station_id_announcement_id)
    return station is None or station.kind != "tts" or station.too_long


def schedule_can_play(document: StoreDocument, schedule_announcement_id: str | None, *, silence: bool) -> bool:
    if silence:
        return True
    return not blocks_playback(document, document.announcement_by_id(schedule_announcement_id))
