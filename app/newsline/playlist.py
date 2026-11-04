"""Ordered pieces of a Newsline bulletin."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.models import Announcement, StoreDocument
from app.newsline.release import friday_from_script, in_air_window, station_today

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


def newsline_unavailable_reason(state=None, *, today: date | None = None) -> str | None:
    """Why the bulletin must not air, or ``None`` if it may.

    A failed latest check, missing stories, or a report whose intended Friday
    is outside the air window (Thursday of that week through the following
    Thursday) all block playback. Last week's files are not used after that.
    """
    from app.newsline.state import load_state

    current = load_state() if state is None else state
    if current.error:
        return "Newsline is not available"
    stories = current.stories_dir()
    if not stories_ready(stories):
        return "Newsline is not available"
    friday = current.release_friday
    if friday is None and stories is not None and current.report_number is not None:
        friday = friday_from_script(stories.parent / f"nsln{current.report_number}.txt")
    if friday is None or not in_air_window(friday, today if today is not None else station_today()):
        return "Newsline is not available"
    return None


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


def resume_piece(bulletin: Announcement, document: StoreDocument) -> Piece | None:
    """Spoken line after a COS busy wait, or ``None`` if unset or too long."""
    item = _spoken(document, bulletin.resume_announcement_id)
    if item is None:
        return None
    if item.too_long:
        log.info("Omitting too-long resume %s", item.name)
        return None
    return Piece(label=item.name, announcement=item)


def _spoken(document: StoreDocument, announcement_id: str | None) -> Announcement | None:
    if not announcement_id:
        return None
    item = document.announcement_by_id(announcement_id)
    if item is None or item.kind != "tts":
        return None
    return item
