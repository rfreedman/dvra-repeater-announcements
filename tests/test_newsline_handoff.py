"""Handoff inspection and quiet-cut placement.

Packing tests do not cover this. These lock landmark matching, where a cut
lands relative to sign-off and opener, and how inspection rewrites boundaries.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.newsline.engine.handoff_cuts import cut_between_landmarks
from app.newsline.engine.handoff_inspect import (
    refine_boundaries_by_inspection,
    refine_handoff_from_inspection,
)
from app.newsline.engine.handoff_landmarks import best_opener, best_signoff
from app.newsline.engine.handoff_window import transcribe_handoff_window
from app.newsline.engine.models import Boundary, Segment, SegmentKind, WordTiming
from app.newsline.engine.script_phrases import (
    signoff_phrases,
    speakable_line_token_lists,
)

_AUDIO = Path("unused.mp3")
_SIGNOFF = "Thanks for listening this is the amateur radio newsline"
_OPENER = "Hams gathered for the annual field day exercise in town"


def _story(index: int, title: str, spoken: str) -> Segment:
    return Segment(
        index,
        title,
        (f"ANCHOR: {spoken}",),
        SegmentKind.STORY,
        spoken,
    )


def _intro() -> Segment:
    spoken = "You are listening to amateur radio newsline right now"
    return Segment(0, "INTRO", (spoken,), SegmentKind.INTRO, spoken)


def _break() -> Segment:
    return Segment(
        3,
        "BREAK HERE: identify your station",
        (),
        SegmentKind.BREAK,
        "identify your station",
    )


def _words(text: str, start: float, step: float = 0.2) -> list[WordTiming]:
    out: list[WordTiming] = []
    t = start
    for word in text.split():
        out.append(WordTiming(word, t, t + step - 0.02))
        t += step
    return out


def _energy(
    spans: tuple[tuple[float, float, float], ...],
    *,
    until: float,
    step: float = 0.01,
) -> list[tuple[float, float]]:
    frames: list[tuple[float, float]] = []
    count = int(round(until / step))
    for i in range(count + 1):
        t = round(i * step, 5)
        level = 0.0
        for start, end, rms in spans:
            if start <= t < end:
                level = rms
                break
        frames.append((t, level))
    return frames


class ScriptPhraseTest(unittest.TestCase):
    def test_signoff_and_opener_lines_come_from_the_script(self) -> None:
        story = _story(1, "Story One", _SIGNOFF)
        phrases = signoff_phrases(story)
        self.assertIn(["amateur", "radio", "newsline"], phrases)
        lines = speakable_line_token_lists(story)
        self.assertEqual(lines[0][:3], ["thanks", "for", "listening"])

        intro = _intro()
        intro_phrases = signoff_phrases(intro)
        self.assertTrue(any(p[-2:] == ["right", "now"] for p in intro_phrases))


class LandmarkMatchTest(unittest.TestCase):
    def test_best_signoff_prefers_the_latest_script_suffix(self) -> None:
        segment = _story(1, "Story One", _SIGNOFF)
        words = _words(_SIGNOFF, 1.0) + _words(_SIGNOFF, 6.0)
        hit = best_signoff(words, segment)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertAlmostEqual(hit[1], words[-1].end)
        self.assertGreater(hit[2], 90.0)

    def test_best_signoff_respects_not_after(self) -> None:
        segment = _story(1, "Story One", _SIGNOFF)
        early = _words(_SIGNOFF, 1.0)
        words = early + _words(_SIGNOFF, 6.0)
        hit = best_signoff(words, segment, not_after=4.0)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertAlmostEqual(hit[1], early[-1].end)

    def test_best_opener_prefers_the_earliest_script_line(self) -> None:
        segment = _story(2, "Story Two", _OPENER)
        words = (
            _words("something else entirely here", 0.2)
            + _words(_OPENER, 4.0)
            + _words(_OPENER, 9.0)
        )
        hit = best_opener(words, segment)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertAlmostEqual(hit[0], 4.0)

    def test_best_opener_rejects_a_wrong_first_token(self) -> None:
        segment = _story(2, "Story Two", _OPENER)
        decoy = _words("zzzz gathered for the annual field", 1.0)
        self.assertIsNone(best_opener(decoy, segment))
        hit = best_opener(decoy + _words(_OPENER, 5.0), segment, not_before=4.0)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertAlmostEqual(hit[0], 5.0)


class QuietCutPlacementTest(unittest.TestCase):
    def test_cut_lands_in_the_quiet_just_before_the_opener(self) -> None:
        signoff_end = 10.0
        opener_start = 11.2
        frames = _energy(
            (
                (0.0, 10.9, 2500.0),
                (10.9, opener_start, 40.0),
                (opener_start, 13.0, 2500.0),
            ),
            until=13.0,
        )
        cut = cut_between_landmarks(frames, signoff_end, opener_start)
        self.assertGreater(cut, 10.9)
        self.assertLess(cut, opener_start)

    def test_abutting_landmarks_cut_after_the_signoff(self) -> None:
        signoff_end = 10.0
        opener_start = 10.1
        frames = _energy(
            (
                (9.5, 10.3, 2500.0),
                (10.3, 10.8, 40.0),
                (10.8, 12.0, 2500.0),
            ),
            until=12.0,
        )
        cut = cut_between_landmarks(frames, signoff_end, opener_start)
        self.assertGreater(cut, signoff_end)
        self.assertLess(cut, 10.8)


class HandoffInspectionTest(unittest.TestCase):
    def test_both_landmarks_place_one_cut_in_the_gap(self) -> None:
        prev = _story(1, "Story One", _SIGNOFF)
        nxt = _story(2, "Story Two", _OPENER)
        words = _words(_SIGNOFF, 9.0) + _words(_OPENER, 12.0)
        frames = _energy(
            (
                (0.0, 11.6, 2500.0),
                (11.6, 12.0, 40.0),
                (12.0, 16.0, 2500.0),
            ),
            until=16.0,
        )
        with patch(
            "app.newsline.engine.handoff_inspect.transcribe_handoff_window",
            return_value=words,
        ):
            cut_a, cut_b, detail = refine_handoff_from_inspection(
                _AUDIO,
                frames,
                11.0,
                prev,
                nxt,
                audio_duration=16.0,
                allow_dual=False,
            )
        self.assertAlmostEqual(cut_a, cut_b)
        self.assertGreater(cut_a, 11.6)
        self.assertLess(cut_a, 12.0)
        self.assertIn("inspect signoff@", detail)

    def test_no_landmarks_keeps_the_provisional_cut(self) -> None:
        prev = _story(1, "Story One", _SIGNOFF)
        nxt = _story(2, "Story Two", _OPENER)
        frames = _energy(((0.0, 16.0, 2500.0),), until=16.0)
        with patch(
            "app.newsline.engine.handoff_inspect.transcribe_handoff_window",
            return_value=[],
        ):
            cut_a, cut_b, detail = refine_handoff_from_inspection(
                _AUDIO,
                frames,
                11.0,
                prev,
                nxt,
                audio_duration=16.0,
            )
        self.assertEqual((cut_a, cut_b), (11.0, 11.0))
        self.assertEqual(detail, "inspect: no landmarks; keep provisional")

    def test_break_handoff_uses_post_beep_quiet_without_asr(self) -> None:
        prev = _break()
        nxt = _story(4, "Story Three", _OPENER)
        # Two sustained tones, a short gap inside the train, then a long rest.
        frames = _energy(
            (
                (18.0, 22.0, 2000.0),
                (22.0, 23.0, 40.0),
                (23.0, 23.5, 8000.0),
                (23.5, 23.8, 40.0),
                (23.8, 24.3, 8000.0),
                (24.3, 25.2, 40.0),
                (25.2, 30.0, 2000.0),
            ),
            until=40.0,
        )

        def _fail(*_args: object, **_kwargs: object) -> list[WordTiming]:
            raise AssertionError("break handoff must not transcribe")

        with patch(
            "app.newsline.engine.handoff_inspect.transcribe_handoff_window",
            side_effect=_fail,
        ):
            cut_a, cut_b, detail = refine_handoff_from_inspection(
                _AUDIO,
                frames,
                24.0,
                prev,
                nxt,
                audio_duration=40.0,
                allow_dual=True,
                keep_end=22.2,
                keep_start=25.0,
            )
        self.assertLess(cut_a, cut_b)
        self.assertGreater(cut_b, 24.3)
        self.assertLess(cut_b, 25.2)
        self.assertTrue(detail.startswith("inspect post-beep"))

    def test_long_gap_keeps_separate_edges(self) -> None:
        prev = _story(1, "Story One", _SIGNOFF)
        nxt = _story(2, "Story Two", _OPENER)
        words = _words(_SIGNOFF, 8.2) + _words(_OPENER, 20.0)
        frames = _energy(
            (
                (0.0, 10.2, 2500.0),
                (10.2, 18.5, 40.0),
                (18.5, 20.0, 40.0),
                (20.0, 24.0, 2500.0),
            ),
            until=24.0,
        )
        with patch(
            "app.newsline.engine.handoff_inspect.transcribe_handoff_window",
            return_value=words,
        ):
            cut_a, cut_b, detail = refine_handoff_from_inspection(
                _AUDIO,
                frames,
                15.0,
                prev,
                nxt,
                audio_duration=24.0,
                allow_dual=True,
            )
        self.assertLess(cut_a, 12.0)
        self.assertGreater(cut_b, 18.0)
        self.assertLess(cut_b, 20.0)
        self.assertIn("inspect dual", detail)


class BoundaryInspectionTest(unittest.TestCase):
    def test_story_pair_keeps_quiet_cut_without_a_later_opener(self) -> None:
        segments = (
            _story(1, "Story One", _SIGNOFF),
            _story(2, "Story Two", _OPENER),
        )
        boundaries = [
            Boundary(1, "Story One", 0.0, 10.0, 90.0, "quiet"),
            Boundary(2, "Story Two", 10.0, 20.0, 90.0, "quiet"),
        ]
        frames = _energy(((0.0, 30.0, 2500.0),), until=30.0)
        with patch(
            "app.newsline.engine.handoff_inspect.transcribe_handoff_window",
            return_value=[],
        ):
            out = refine_boundaries_by_inspection(
                boundaries,
                segments,
                _AUDIO,
                frames,
                30.0,
            )
        self.assertEqual(out[0].end, 10.0)
        self.assertEqual(out[1].start, 10.0)
        self.assertEqual(out[1].end, 30.0)

    def test_story_pair_moves_cut_before_opener_when_residual_is_loud(self) -> None:
        segments = (
            _story(1, "Story One", _SIGNOFF),
            _story(2, "Story Two", _OPENER),
        )
        boundaries = [
            Boundary(1, "Story One", 0.0, 10.0, 90.0, "quiet"),
            Boundary(2, "Story Two", 10.0, 20.0, 90.0, "quiet"),
        ]
        frames = _energy(
            (
                (0.0, 10.1, 2500.0),
                (10.1, 11.5, 2000.0),
                (11.5, 12.0, 40.0),
                (12.0, 30.0, 2500.0),
            ),
            until=30.0,
        )
        with patch(
            "app.newsline.engine.handoff_inspect.transcribe_handoff_window",
            return_value=_words(_OPENER, 12.0),
        ):
            out = refine_boundaries_by_inspection(
                boundaries,
                segments,
                _AUDIO,
                frames,
                30.0,
            )
        self.assertGreater(out[0].end, 11.5)
        self.assertLess(out[0].end, 12.0)
        self.assertAlmostEqual(out[0].end, out[1].start)
        self.assertIn("story residual", out[0].anchor_snippet)


class _WindowWord:
    word: str
    start: float
    end: float

    def __init__(self, word: str, start: float, end: float) -> None:
        self.word = word
        self.start = start
        self.end = end


class _WindowSegment:
    words: list[_WindowWord]

    def __init__(self, words: list[_WindowWord]) -> None:
        self.words = words


class _WindowModel:
    def transcribe(
        self,
        pcm: object,
        *,
        word_timestamps: bool,
        language: str,
        vad_filter: bool,
    ) -> tuple[list[_WindowSegment], None]:
        _ = pcm, word_timestamps, language, vad_filter
        return ([_WindowSegment([_WindowWord(" hello ", 0.2, 0.45)])], None)


class WindowTranscriptionTest(unittest.TestCase):
    def test_word_times_are_offset_onto_the_master_timeline(self) -> None:
        def _decode(_audio: Path, _t0: float, _t1: float, _out: Path) -> None:
            return None

        with (
            patch(
                "app.newsline.engine.handoff_window._decode_window_wav",
                side_effect=_decode,
            ),
            patch(
                "app.newsline.engine.handoff_window.pcm_float32_from_wav",
                return_value=[0.0],
            ),
            patch(
                "app.newsline.engine.whisper_model.get_whisper_model",
                return_value=_WindowModel(),
            ),
        ):
            words = transcribe_handoff_window(_AUDIO, 40.0, 48.0, model_name="tiny.en")
        self.assertEqual(len(words), 1)
        self.assertEqual(words[0].word, "hello")
        self.assertAlmostEqual(words[0].start, 40.2)
        self.assertAlmostEqual(words[0].end, 40.45)


if __name__ == "__main__":
    _ = unittest.main()
