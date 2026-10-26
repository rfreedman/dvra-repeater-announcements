"""Coverage checks for speakable blocks that forced alignment did not span."""

from __future__ import annotations

import unittest

from app.newsline.engine.forced_align import validate_forced_coverage
from app.newsline.engine.models import Boundary, Segment, SegmentKind


def _seg(
    index: int,
    title: str,
    kind: SegmentKind,
    speakable: str,
) -> Segment:
    return Segment(index, title, (), kind, speakable)


class ValidateForcedCoverageTest(unittest.TestCase):
    def test_missing_span_for_speakable_story(self) -> None:
        segments = (
            _seg(0, "INTRO", SegmentKind.INTRO, "Amateur Radio Newsline report."),
            _seg(1, "BILLBOARD CART", SegmentKind.BILLBOARD, ""),
            _seg(2, "Story One", SegmentKind.STORY, "Hams gathered for the drill."),
            _seg(3, "BREAK HERE", SegmentKind.BREAK, "Time for you to identify your station."),
            _seg(4, "NEWSCAST CLOSE", SegmentKind.CLOSE, ""),
        )
        spans = [
            (0, 0.0, 10.0, "intro"),
            (4, 40.0, 50.0, "close has no speakable tokens so a span is optional"),
        ]
        boundaries = (
            Boundary(0, "INTRO", 0.0, 10.0, 100.0, ""),
            Boundary(2, "Story One", 10.0, 40.0, 100.0, ""),
        )
        issues = validate_forced_coverage(segments, spans, boundaries)
        self.assertEqual(
            issues,
            ["missing forced-align span for [2] Story One"],
        )

    def test_billboard_break_and_empty_text_need_no_span(self) -> None:
        segments = (
            _seg(0, "INTRO", SegmentKind.INTRO, "Amateur Radio Newsline report."),
            _seg(1, "BILLBOARD CART", SegmentKind.BILLBOARD, "unspoken cart"),
            _seg(2, "BREAK HERE", SegmentKind.BREAK, "Time for you to identify your station."),
            _seg(3, "Silent label", SegmentKind.STORY, "   "),
        )
        spans = [(0, 0.0, 12.0, "intro")]
        boundaries = (Boundary(0, "INTRO", 0.0, 12.0, 100.0, ""),)
        self.assertEqual(validate_forced_coverage(segments, spans, boundaries), [])


if __name__ == "__main__":
    unittest.main()
