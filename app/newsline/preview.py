"""Browser preview of the latest split Newsline report."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

from app.audio import PcmChunk
from app.config import NEWSLINE_DIR
from app.models import Announcement
from app.newsline.decode import SAMPLE_RATE, ffmpeg_available, mp3_to_chunks
from app.newsline.playlist import Piece, build_playlist, stories_ready
from app.newsline.state import load_state
from app.store import get_store


class PreviewError(Exception):
    """The editor asked to play a report that is not available."""


def latest_ready_stories(root: Path | None = None) -> Path | None:
    base = root or NEWSLINE_DIR
    state = load_state(root)
    stories = state.stories_dir(root)
    if stories_ready(stories):
        return stories
    found: list[Path] = []
    if base.is_dir():
        for child in base.iterdir():
            if not child.is_dir() or not child.name.isdigit():
                continue
            path = child / "stories"
            if stories_ready(path):
                found.append(path)
    if not found:
        return None
    return max(found, key=lambda path: int(path.parent.name))


def frame_piece_pcm(pcm: bytes) -> bytes:
    """Length-prefixed PCM so the editor can pause between pieces."""
    return struct.pack(">I", len(pcm)) + pcm


def frame_named_piece(label: str, pcm: bytes) -> bytes:
    """UTF-8 name, then length-prefixed PCM, each with a uint32 length."""
    name = (label or "").encode("utf-8")
    return struct.pack(">I", len(name)) + name + frame_piece_pcm(pcm)


def _playlist(
    *,
    opening_announcement_id: str | None = None,
    station_id_announcement_id: str | None = None,
    closing_announcement_id: str | None = None,
    root: Path | None = None,
) -> list[Piece]:
    stories = latest_ready_stories(root)
    if stories is None:
        raise PreviewError("No Newsline report is ready to play.")
    if not ffmpeg_available():
        raise OSError("ffmpeg and ffprobe are required to play Newsline audio")
    document = get_store().document()
    bulletin = Announcement.model_construct(
        name="Preview",
        kind="newsline",
        opening_announcement_id=opening_announcement_id,
        station_id_announcement_id=station_id_announcement_id,
        closing_announcement_id=closing_announcement_id,
    )
    return build_playlist(bulletin, document, stories, require_station=False)


def _pcm_for_piece(piece: Piece) -> bytes:
    from app.pcm_cache import chunks_for_announcement

    chunks: list[PcmChunk]
    if piece.path is not None:
        chunks = mp3_to_chunks(piece.path, sample_rate=SAMPLE_RATE)
    elif piece.announcement is not None:
        chunks = list(chunks_for_announcement(piece.announcement))
    else:
        return b""
    return b"".join(chunk.pcm_int16 for chunk in chunks)


def preview_chunks(
    *,
    opening_announcement_id: str | None = None,
    station_id_announcement_id: str | None = None,
    closing_announcement_id: str | None = None,
    root: Path | None = None,
) -> tuple[int, Iterator[PcmChunk]]:
    pieces = _playlist(
        opening_announcement_id=opening_announcement_id,
        station_id_announcement_id=station_id_announcement_id,
        closing_announcement_id=closing_announcement_id,
        root=root,
    )

    def chunks() -> Iterator[PcmChunk]:
        from app.pcm_cache import chunks_for_announcement

        for piece in pieces:
            if piece.path is not None:
                yield from mp3_to_chunks(piece.path, sample_rate=SAMPLE_RATE)
            elif piece.announcement is not None:
                yield from chunks_for_announcement(piece.announcement)

    return SAMPLE_RATE, chunks()


def preview_frames(
    *,
    opening_announcement_id: str | None = None,
    station_id_announcement_id: str | None = None,
    closing_announcement_id: str | None = None,
    root: Path | None = None,
) -> tuple[int, Iterator[bytes]]:
    pieces = _playlist(
        opening_announcement_id=opening_announcement_id,
        station_id_announcement_id=station_id_announcement_id,
        closing_announcement_id=closing_announcement_id,
        root=root,
    )

    def frames() -> Iterator[bytes]:
        for piece in pieces:
            pcm = _pcm_for_piece(piece)
            if pcm:
                yield frame_named_piece(piece.label, pcm)

    return SAMPLE_RATE, frames()
