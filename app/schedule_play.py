"""Whether an announcement may own a slot (length or missing Newsline)."""

from __future__ import annotations

from app.models import Announcement, StoreDocument
from app.spoken_length import blocks_playback


def newsline_blocks_slot(announcement: Announcement | None) -> bool:
    if announcement is None or announcement.kind != "newsline":
        return False
    from app.newsline.playlist import newsline_unavailable_reason

    return newsline_unavailable_reason() is not None


def announcement_cannot_play(
    document: StoreDocument, announcement: Announcement | None
) -> bool:
    """True when this item must not own the slot (length, missing ID, or no Newsline)."""
    if blocks_playback(document, announcement):
        return True
    return newsline_blocks_slot(announcement)
