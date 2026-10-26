"""Ordered pieces of a Newsline bulletin."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.models import Announcement, StoreDocument

log = logging.getLogger("app.newsline")


@dataclass(frozen=True)
class Piece:
    label: str
    announcement: Announcement | None = None
    path: Path | None = None


def story_files(stories_dir: Path) -> list[Path]:
    if not stories_dir.is_dir():
        return []
    return sorted(path for path in stories_dir.glob("*.mp3") if path.is_file())


def stories_ready(stories_dir: Path | None) -> bool:
    if stories_dir is None:
        return False
    return any(not path.name.endswith("station-id.mp3") for path in story_files(stories_dir))


def build_playlist(
    bulletin: Announcement,
    document: StoreDocument,
    stories_dir: Path,
    *,
    require_station: bool = True,
) -> list[Piece]:
    pieces: list[Piece] = []
    opening = _spoken(document, bulletin.opening_announcement_id)
    if opening is not None:
        if opening.too_long:
            log.info("Omitting too-long opening %s", opening.name)
        else:
            pieces.append(Piece(label=opening.name, announcement=opening))
    station = _spoken(document, bulletin.station_id_announcement_id)
    for path in story_files(stories_dir):
        if path.name.endswith("station-id.mp3"):
            if station is None or station.too_long:
                if require_station:
                    raise ValueError("Station ID announcement is missing or too long")
                continue
            pieces.append(Piece(label=station.name, announcement=station))
            continue
        pieces.append(Piece(label=path.name, path=path))
    closing = _spoken(document, bulletin.closing_announcement_id)
    if closing is not None:
        if closing.too_long:
            log.info("Omitting too-long closing %s", closing.name)
        else:
            pieces.append(Piece(label=closing.name, announcement=closing))
    return pieces


def _spoken(document: StoreDocument, announcement_id: str | None) -> Announcement | None:
    if not announcement_id:
        return None
    item = document.announcement_by_id(announcement_id)
    if item is None or item.kind != "tts":
        return None
    return item
