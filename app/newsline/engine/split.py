from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.newsline.engine.models import (
    Boundary,
    Segment,
    SegmentKind,
    WordTiming,
)
from app.newsline.engine.transcript import break_follows_segment

_INTRO_BILLBOARD_SLUG = "intro_and_billboard"
_INTRO_BILLBOARD_STORY_SLUG = "intro_billboard_first_story"
# Default repeater timeout (seconds). Packs must finish 2s under this.
_DEFAULT_REPEATER_TIMEOUT_SEC = 180
_PACK_TIMEOUT_MARGIN_SEC = 2

_PACKABLE_KINDS = frozenset(
    {
        SegmentKind.INTRO,
        SegmentKind.BILLBOARD,
        SegmentKind.STORY,
        SegmentKind.CLOSE,
    }
)


def probe_duration(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def slugify(title: str, max_len: int = 60) -> str:
    text = title.lower()
    text = re.sub(r"^kicker:\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    if len(text) > max_len:
        text = text[:max_len].rstrip("_")
    return text or "segment"


def export_boundaries_json(path: Path, boundaries: tuple[Boundary, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "segment_index": b.segment_index,
            "title": b.title,
            "start": round(b.start, 3),
            "end": round(b.end, 3),
            "match_score": round(b.match_score, 2),
            "anchor_snippet": b.anchor_snippet,
        }
        for b in boundaries
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_alignment_report(path: Path, boundaries: tuple[Boundary, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["Alignment report", "=" * 40, ""]
    for b in boundaries:
        lines.append(f"[{b.segment_index:02d}] {b.title}")
        lines.append(f"  {b.start:.2f}s – {b.end:.2f}s  (score {b.match_score:.1f})")
        lines.append(f"  anchor: {b.anchor_snippet}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


@dataclass(frozen=True)
class ExportSlot:
    """One on-disk MP3 (or station-id placeholder)."""

    path: Path
    boundaries: tuple[Boundary, ...]
    is_station_id: bool = False

    @property
    def primary(self) -> Boundary:
        return self.boundaries[0]

    @property
    def start(self) -> float:
        return self.boundaries[0].start

    @property
    def end(self) -> float:
        return self.boundaries[-1].end

    @property
    def title(self) -> str:
        n = len(self.boundaries)
        first = self.boundaries[0].title.upper()
        if n >= 3 and first.startswith("INTRO"):
            return "INTRO BILLBOARD FIRST STORY"
        if n == 2 and first.startswith("INTRO"):
            return "INTRO AND BILLBOARD"
        if n > 1:
            return f"{self.boundaries[0].title} (+{n - 1})"
        return self.boundaries[0].title


def pack_limit_sec(repeater_timeout: int) -> int | None:
    """
    Effective max packed duration.

    ``repeater_timeout`` is the repeater timeout in whole seconds; packs must
    finish ``_PACK_TIMEOUT_MARGIN_SEC`` under it. ``0`` / negative disables packing.
    """
    if repeater_timeout <= 0:
        return None
    return max(1, repeater_timeout - _PACK_TIMEOUT_MARGIN_SEC)


def _slot_filename(
    report_id: int,
    file_index: int,
    pack: tuple[Boundary, ...],
    seg_by: dict[int, Segment],
) -> str:
    kinds = [seg_by[b.segment_index].kind for b in pack if b.segment_index in seg_by]
    if (
        len(pack) == 2
        and kinds == [SegmentKind.INTRO, SegmentKind.BILLBOARD]
    ):
        slug = _INTRO_BILLBOARD_SLUG
    elif (
        len(pack) >= 3
        and kinds[:3]
        == [SegmentKind.INTRO, SegmentKind.BILLBOARD, SegmentKind.STORY]
    ):
        slug = _INTRO_BILLBOARD_STORY_SLUG
    elif len(pack) == 1:
        slug = slugify(pack[0].title)
    else:
        slug = f"{slugify(pack[0].title)}_plus_{len(pack) - 1}"
    return f"{report_id:04d}_{file_index:02d}_{slug}.mp3"


def plan_export_slots(
    output_dir: Path,
    report_id: int,
    boundaries: tuple[Boundary, ...],
    segments: tuple[Segment, ...],
    *,
    repeater_timeout: int = _DEFAULT_REPEATER_TIMEOUT_SEC,
) -> list[ExportSlot]:
    """
    Map export boundaries to MP3 paths.

    Greedily packs consecutive INTRO/BILLBOARD/STORY/CLOSE spans while
    ``end - start < repeater_timeout - 2``. Never packs across BREAK; inserts
    an empty station-id placeholder after the pack that precedes a BREAK.
    """
    seg_by = {s.index: s for s in segments}
    limit = pack_limit_sec(repeater_timeout)
    slots: list[ExportSlot] = []
    file_index = 1
    i = 0
    while i < len(boundaries):
        b = boundaries[i]
        seg = seg_by.get(b.segment_index)

        # BREAK (and any non-packable) always alone.
        if seg is None or seg.kind not in _PACKABLE_KINDS or limit is None:
            pack = (b,)
            slots.append(
                ExportSlot(
                    path=output_dir
                    / f"{report_id:04d}_{file_index:02d}_{slugify(b.title)}.mp3",
                    boundaries=pack,
                    is_station_id=False,
                )
            )
            file_index += 1
            if segments and break_follows_segment(segments, b.segment_index):
                slots.append(
                    ExportSlot(
                        path=output_dir
                        / f"{report_id:04d}_{file_index:02d}_station-id.mp3",
                        boundaries=(b,),
                        is_station_id=True,
                    )
                )
                file_index += 1
            i += 1
            continue

        pack_list = [b]
        j = i + 1
        while j < len(boundaries):
            # BREAK is omitted from export boundaries; stop before crossing it
            # so the station-id placeholder is inserted after this pack.
            if segments and break_follows_segment(
                segments, pack_list[-1].segment_index
            ):
                break
            nxt = boundaries[j]
            nxt_seg = seg_by.get(nxt.segment_index)
            # Non-packable kinds end the pack (BREAK is never in _PACKABLE_KINDS).
            if nxt_seg is None or nxt_seg.kind not in _PACKABLE_KINDS:
                break
            if (nxt.end - pack_list[0].start) >= limit:
                break
            pack_list.append(nxt)
            j += 1

        pack = tuple(pack_list)
        slots.append(
            ExportSlot(
                path=output_dir / _slot_filename(report_id, file_index, pack, seg_by),
                boundaries=pack,
                is_station_id=False,
            )
        )
        file_index += 1
        last = pack[-1]
        if segments and break_follows_segment(segments, last.segment_index):
            slots.append(
                ExportSlot(
                    path=output_dir
                    / f"{report_id:04d}_{file_index:02d}_station-id.mp3",
                    boundaries=(last,),
                    is_station_id=True,
                )
            )
            file_index += 1
        i = j
    return slots


def _ffmpeg_slice(
    audio_path: Path,
    out_path: Path,
    start: float,
    end: float,
    *,
    reencode: bool,
) -> None:
    duration = max(0.05, end - start)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(audio_path),
        "-t",
        f"{duration:.3f}",
    ]
    if reencode:
        # Mono matches source (AR Newsline is mono); fixed rate avoids
        # intermittent libmp3lame "plane padding" failures.
        cmd.extend(["-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-q:a", "2"])
    else:
        cmd.extend(["-acodec", "copy"])
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True)


def _remove_unplanned_mp3s(output_dir: Path, planned: set[Path]) -> int:
    """Delete MP3s in ``output_dir`` that are not part of the current slot plan."""
    planned_resolved = {path.resolve() for path in planned}
    removed = 0
    for path in output_dir.glob("*.mp3"):
        if path.resolve() not in planned_resolved:
            path.unlink()
            removed += 1
    return removed


def slice_audio(
    audio_path: Path,
    boundaries: tuple[Boundary, ...],
    output_dir: Path,
    report_id: int,
    *,
    segments: tuple[Segment, ...] = (),
    words: list[WordTiming] | None = None,
    seg_by_index: dict[int, Segment] | None = None,
    reencode: bool = False,
    only_segment_indices: set[int] | None = None,
    max_workers: int = 4,
    repeater_timeout: int = _DEFAULT_REPEATER_TIMEOUT_SEC,
) -> tuple[str, ...]:
    """
    Export MP3s for planned slots.

    When ``only_segment_indices`` is set, only slots whose boundaries touch
    those segment indices are re-encoded (station-id placeholders still written).

    A full export (``only_segment_indices`` unset) deletes ``*.mp3`` files in
    ``output_dir`` that are not in the new plan, after every planned file has
    been written. Selective QA re-export does not delete siblings.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.newsline.engine.progress import log, progress

    del words, seg_by_index  # ends come from boundaries (inspect already applied)
    output_dir.mkdir(parents=True, exist_ok=True)
    slots = plan_export_slots(
        output_dir,
        report_id,
        boundaries,
        segments,
        repeater_timeout=repeater_timeout,
    )
    outputs: list[str] = []

    def _slot_touches(slot: ExportSlot) -> bool:
        if only_segment_indices is None:
            return True
        return any(b.segment_index in only_segment_indices for b in slot.boundaries)

    work: list[tuple[ExportSlot, float, float]] = []
    for slot in slots:
        if slot.is_station_id:
            # Always (re)write the zero-byte placeholder so selective re-export
            # and packing renumbers cannot leave a missing station-id cue.
            slot.path.write_bytes(b"")
            outputs.append(str(slot.path))
            continue
        outputs.append(str(slot.path))
        if not _slot_touches(slot):
            continue
        start = max(0.0, slot.start)
        end = slot.end
        work.append((slot, start, end))

    if work:
        total = len(work)
        if only_segment_indices is not None:
            log(f"  selective re-export: {total} of {len(slots)} slots")

        # Parallel ffmpeg is quality-neutral (independent slices of the same master).
        workers = max(1, min(max_workers, total))
        done = 0

        def _one(item: tuple[ExportSlot, float, float]) -> str:
            slot, start, end = item
            _ffmpeg_slice(audio_path, slot.path, start, end, reencode=reencode)
            return slot.path.name

        if workers == 1:
            for slot, start, end in work:
                done += 1
                progress(done, total, f"ffmpeg {slot.path.name} ({start:.1f}–{end:.1f}s)")
                _ffmpeg_slice(audio_path, slot.path, start, end, reencode=reencode)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_one, item): item for item in work}
                for fut in as_completed(futures):
                    slot, start, end = futures[fut]
                    fut.result()
                    done += 1
                    progress(
                        done, total, f"ffmpeg {slot.path.name} ({start:.1f}–{end:.1f}s)"
                    )

    # Only after a complete full export, so a failed ffmpeg run keeps the previous set.
    if only_segment_indices is None:
        removed = _remove_unplanned_mp3s(output_dir, {slot.path for slot in slots})
        if removed:
            log(f"  removed {removed} MP3(s) not in the current export plan")

    return tuple(outputs)
