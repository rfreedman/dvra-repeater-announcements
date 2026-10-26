"""Packing and stale-MP3 cleanup. Does not cover handoff or quiet-cut behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Never
from unittest.mock import patch

from app.newsline.engine.models import Boundary, Segment, SegmentKind
from app.newsline.engine.split import (
    _remove_unplanned_mp3s,
    plan_export_slots,
    slice_audio,
)


def _seg(index: int, title: str, kind: SegmentKind) -> Segment:
    return Segment(index, title, (), kind, "")


def _bound(index: int, title: str, start: float, end: float) -> Boundary:
    return Boundary(index, title, start, end, 1.0, "")


def _episode() -> tuple[tuple[Segment, ...], tuple[Boundary, ...]]:
    segments = (
        _seg(0, "INTRO", SegmentKind.INTRO),
        _seg(1, "BILLBOARD CART", SegmentKind.BILLBOARD),
        _seg(2, "Story One", SegmentKind.STORY),
        _seg(3, "Story Two", SegmentKind.STORY),
        _seg(4, "BREAK HERE", SegmentKind.BREAK),
        _seg(5, "Story Three", SegmentKind.STORY),
        _seg(6, "NEWSCAST CLOSE", SegmentKind.CLOSE),
    )
    # Short enough that a duration-only packer would cross the break.
    boundaries = (
        _bound(0, "INTRO", 0.0, 5.0),
        _bound(1, "BILLBOARD CART", 5.0, 10.0),
        _bound(2, "Story One", 10.0, 30.0),
        _bound(3, "Story Two", 30.0, 50.0),
        _bound(5, "Story Three", 55.0, 75.0),
        _bound(6, "NEWSCAST CLOSE", 75.0, 90.0),
    )
    return segments, boundaries


class PlanExportSlotsTest(unittest.TestCase):
    def test_does_not_pack_across_break(self) -> None:
        segments, boundaries = _episode()
        slots = plan_export_slots(
            Path("/tmp"), 2540, boundaries, segments, repeater_timeout=180
        )
        for slot in slots:
            if slot.is_station_id:
                continue
            indexes = {b.segment_index for b in slot.boundaries}
            self.assertFalse({3, 5} <= indexes)

    def test_inserts_one_station_id_per_break(self) -> None:
        segments, boundaries = _episode()
        slots = plan_export_slots(
            Path("/tmp"), 2540, boundaries, segments, repeater_timeout=180
        )
        sids = [slot for slot in slots if slot.is_station_id]
        self.assertEqual(len(sids), 1)
        self.assertEqual(sids[0].path.name, "2540_02_station-id.mp3")
        self.assertEqual(sids[0].boundaries[0].segment_index, 3)

    def test_timeout_zero_disables_packing(self) -> None:
        segments, boundaries = _episode()
        slots = plan_export_slots(
            Path("/tmp"), 2540, boundaries, segments, repeater_timeout=0
        )
        audio = [slot for slot in slots if not slot.is_station_id]
        self.assertTrue(all(len(slot.boundaries) == 1 for slot in audio))
        self.assertEqual(sum(1 for slot in slots if slot.is_station_id), 1)

    def test_packs_under_timeout_minus_two(self) -> None:
        segments = (
            _seg(0, "Story One", SegmentKind.STORY),
            _seg(1, "Story Two", SegmentKind.STORY),
            _seg(2, "Story Three", SegmentKind.STORY),
        )
        boundaries = (
            _bound(0, "Story One", 0.0, 100.0),
            _bound(1, "Story Two", 100.0, 170.0),
            _bound(2, "Story Three", 170.0, 200.0),
        )
        slots = plan_export_slots(
            Path("/tmp"), 9, boundaries, segments, repeater_timeout=180
        )
        self.assertEqual(len(slots), 2)
        self.assertEqual([b.segment_index for b in slots[0].boundaries], [0, 1])
        self.assertEqual([b.segment_index for b in slots[1].boundaries], [2])
        self.assertLess(slots[0].end - slots[0].start, 178)
        self.assertGreaterEqual(200.0 - 0.0, 178)


class RemoveUnplannedMp3sTest(unittest.TestCase):
    def test_deletes_only_unplanned_mp3s(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            keep = out / "2540_01_intro.mp3"
            stale = out / "2540_07_old_story.mp3"
            notes = out / "run_summary.txt"
            keep.write_bytes(b"keep")
            stale.write_bytes(b"stale")
            notes.write_text("keep me", encoding="utf-8")
            removed = _remove_unplanned_mp3s(out, {keep})
            self.assertEqual(removed, 1)
            self.assertTrue(keep.is_file())
            self.assertFalse(stale.exists())
            self.assertEqual(notes.read_text(encoding="utf-8"), "keep me")


class SliceAudioCleanupTest(unittest.TestCase):
    def _slots(self, output_dir: Path) -> list:
        segments, boundaries = _episode()
        return plan_export_slots(
            output_dir, 2540, boundaries, segments, repeater_timeout=0
        )

    def test_selective_reexport_keeps_files_it_did_not_rewrite(self) -> None:
        segments, boundaries = _episode()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            slots = self._slots(out)
            previous = b"previous"
            for slot in slots:
                if not slot.is_station_id:
                    slot.path.write_bytes(previous)
            stale = out / "2540_99_old_plan.mp3"
            stale.write_bytes(b"stale")
            rewritten = next(
                slot
                for slot in slots
                if not slot.is_station_id and slot.boundaries[0].segment_index == 2
            )

            def _fake_ffmpeg(
                audio_path: Path,
                out_path: Path,
                start: float,
                end: float,
                *,
                reencode: bool,
            ) -> None:
                del audio_path, start, end, reencode
                out_path.write_bytes(b"rewritten")

            with patch(
                "app.newsline.engine.split._ffmpeg_slice",
                side_effect=_fake_ffmpeg,
            ) as ffmpeg:
                slice_audio(
                    Path("audio/Report2540.mp3"),
                    boundaries,
                    out,
                    2540,
                    segments=segments,
                    only_segment_indices={2},
                    max_workers=1,
                    repeater_timeout=0,
                )

            self.assertEqual(ffmpeg.call_count, 1)
            self.assertEqual(rewritten.path.read_bytes(), b"rewritten")
            for slot in slots:
                if slot.is_station_id or slot.path == rewritten.path:
                    continue
                self.assertEqual(slot.path.read_bytes(), previous, slot.path.name)
            self.assertEqual(stale.read_bytes(), b"stale")

    def test_failed_ffmpeg_leaves_previous_mp3s(self) -> None:
        segments, boundaries = _episode()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            slots = self._slots(out)
            previous = b"previous"
            for slot in slots:
                slot.path.write_bytes(previous)
            stale = out / "2540_99_old_plan.mp3"
            stale.write_bytes(b"stale")
            notes = out / "run_summary.txt"
            notes.write_text("keep me", encoding="utf-8")

            def _fail(
                audio_path: Path,
                out_path: Path,
                start: float,
                end: float,
                *,
                reencode: bool,
            ) -> Never:
                del audio_path, out_path, start, end, reencode
                raise RuntimeError("ffmpeg failed")

            with (
                patch(
                    "app.newsline.engine.split._ffmpeg_slice",
                    side_effect=_fail,
                ),
                self.assertRaises(RuntimeError),
            ):
                slice_audio(
                    Path("audio/Report2540.mp3"),
                    boundaries,
                    out,
                    2540,
                    segments=segments,
                    max_workers=1,
                    repeater_timeout=0,
                )

            for slot in slots:
                if slot.is_station_id:
                    continue
                self.assertEqual(slot.path.read_bytes(), previous, slot.path.name)
            self.assertEqual(stale.read_bytes(), b"stale")
            self.assertEqual(notes.read_text(encoding="utf-8"), "keep me")


if __name__ == "__main__":
    unittest.main()
