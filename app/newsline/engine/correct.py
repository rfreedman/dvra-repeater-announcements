from __future__ import annotations

import re

from app.newsline.engine.align import (
    _POST_BILLBOARD_LEAD_SEC,
    _SIGNOFF_PAD_SEC,
    _START_LEAD_SEC,
    _flatten_words,
    _last_signoff_end_in_range,
    _opening_speech_time,
    estimated_min_duration,
)
from app.newsline.engine.models import Boundary, Segment, SegmentKind, WordTiming
from app.newsline.engine.verify import ExportBoundaryIssue
from app.newsline.engine.verify_slices import parse_slice_global_cut


def _boundary_index(boundaries: list[Boundary], segment_index: int) -> int | None:
    for i, b in enumerate(boundaries):
        if b.segment_index == segment_index:
            return i
    return None


def _handoff_start(
    boundaries: list[Boundary],
    pair_index: int,
    segments: tuple[Segment, ...],
    words: list[WordTiming],
    tokens: list[str],
    word_for_token: list[int],
    *,
    opener_min: float,
) -> float | None:
    """Suggested start time for boundaries[pair_index + 1] from Whisper openers."""
    if pair_index < 0 or pair_index + 1 >= len(boundaries):
        return None
    curr = boundaries[pair_index]
    nxt = boundaries[pair_index + 1]
    seg_by_index = {s.index: s for s in segments}
    kind_by_index = {s.index: s.kind for s in segments}
    nxt_seg = seg_by_index.get(nxt.segment_index)
    if nxt_seg is None or nxt_seg.kind not in (SegmentKind.STORY, SegmentKind.CLOSE):
        return None
    prev_kind = kind_by_index.get(curr.segment_index)
    if prev_kind not in (
        SegmentKind.INTRO,
        SegmentKind.BILLBOARD,
        SegmentKind.STORY,
        SegmentKind.BREAK,
    ):
        return None
    lead = (
        _POST_BILLBOARD_LEAD_SEC
        if prev_kind in (SegmentKind.BREAK, SegmentKind.BILLBOARD)
        else _START_LEAD_SEC
    )
    not_before = curr.start
    if prev_kind == SegmentKind.BILLBOARD:
        not_before = curr.start + 0.05
    elif prev_kind == SegmentKind.STORY:
        prev_seg = seg_by_index.get(curr.segment_index)
        if prev_seg is not None:
            min_t = curr.start + estimated_min_duration(prev_seg) * 0.82
            not_before = max(not_before, min_t)
    open_t = _opening_speech_time(
        nxt_seg,
        words,
        tokens,
        word_for_token,
        not_before,
        min_score=opener_min,
    )
    if open_t is None:
        return None
    split = max(curr.start + 0.5, open_t - lead)
    if prev_kind == SegmentKind.STORY:
        prev_seg = seg_by_index.get(curr.segment_index)
        if prev_seg is not None:
            signoff_end = _last_signoff_end_in_range(
                words,
                curr.start + 0.5,
                open_t + 2.0,
                segment=prev_seg,
            )
            if signoff_end is not None:
                split = max(
                    split,
                    min(signoff_end + _SIGNOFF_PAD_SEC, open_t - 0.04),
                )
    return min(split, open_t - 0.02)


def _set_boundary_start(
    boundaries: list[Boundary],
    index: int,
    start: float,
    *,
    allow_earlier: bool = False,
) -> float:
    """Monotonic start update; returns applied delta."""
    if index <= 0:
        floor = 0.0
    else:
        floor = boundaries[index - 1].start + 0.5
    new_start = max(floor, start)
    old = boundaries[index].start
    if not allow_earlier:
        new_start = max(new_start, old)
    if abs(new_start - old) <= 0.001:
        return 0.0
    b = boundaries[index]
    boundaries[index] = Boundary(
        segment_index=b.segment_index,
        title=b.title,
        start=new_start,
        end=b.end,
        match_score=b.match_score,
        anchor_snippet=b.anchor_snippet,
    )
    return abs(new_start - old)


def _set_boundary_end(
    boundaries: list[Boundary],
    index: int,
    end: float,
    *,
    allow_earlier: bool = False,
) -> float:
    b = boundaries[index]
    floor = b.start + 0.5
    if index + 1 < len(boundaries):
        ceiling = boundaries[index + 1].end
        end = min(end, ceiling)
    new_end = max(floor, end)
    old = b.end
    if not allow_earlier:
        new_end = max(new_end, old)
    if abs(new_end - old) <= 0.001:
        return 0.0
    boundaries[index] = Boundary(
        segment_index=b.segment_index,
        title=b.title,
        start=b.start,
        end=new_end,
        match_score=b.match_score,
        anchor_snippet=b.anchor_snippet,
    )
    return abs(new_end - old)


_SLICE_ONLY_CODES = frozenset(
    {
        "slice_next_in_tail",
        "slice_prev_signoff_in_head",
        "slice_opener_lead_clipped",
        "slice_break_in_head",
        "slice_missing_opener",
        "handoff_landmarks_disagree",
        "cut_in_speech",
    }
)


def apply_boundary_corrections(
    boundaries: list[Boundary],
    issues: list[ExportBoundaryIssue],
    segments: tuple[Segment, ...],
    words: list[WordTiming],
    *,
    min_score: float,
    pass_num: int,
    codes: frozenset[str] | None = None,
) -> float:
    """Adjust export boundaries from slice QA issues; no full re-align."""
    if not issues:
        return 0.0
    tokens, word_for_token = _flatten_words(words)
    opener_min = max(55.0, min_score - 15.0 - 5.0 * max(0, pass_num - 2))
    max_delta = 0.0
    seen: set[tuple[int, str]] = set()
    allowed = codes if codes is not None else _SLICE_ONLY_CODES

    for issue in issues:
        key = (issue.segment_index, issue.code)
        if key in seen:
            continue
        if issue.code not in allowed:
            continue
        seen.add(key)
        idx = _boundary_index(boundaries, issue.segment_index)
        if idx is None:
            continue

        if issue.code in ("slice_missing_opener", "slice_break_in_head"):
            if idx > 0:
                split = _handoff_start(
                    boundaries, idx - 1, segments, words, tokens, word_for_token,
                    opener_min=opener_min,
                )
                if split is not None:
                    delta = _set_boundary_start(
                        boundaries, idx, split, allow_earlier=True
                    )
                    max_delta = max(max_delta, delta)
            continue

        if issue.code == "slice_opener_lead_clipped":
            m = re.search(r"master at ([\d.]+)s", issue.detail)
            if m is not None:
                t = float(m.group(1))
                delta = _set_boundary_start(boundaries, idx, t, allow_earlier=True)
                max_delta = max(max_delta, delta)
                if idx > 0:
                    delta = _set_boundary_end(
                        boundaries, idx - 1, t, allow_earlier=True
                    )
                    max_delta = max(max_delta, delta)
            continue

        if issue.code == "slice_prev_signoff_in_head":
            cut = parse_slice_global_cut(issue.detail)
            if cut is not None and idx > 0:
                # Move shared cut later: prev keeps callsign, next starts at opener.
                delta = _set_boundary_start(boundaries, idx, cut, allow_earlier=True)
                max_delta = max(max_delta, delta)
                delta = _set_boundary_end(
                    boundaries, idx - 1, cut, allow_earlier=True
                )
                max_delta = max(max_delta, delta)
            continue

        if issue.code == "slice_next_in_tail":
            cut = parse_slice_global_cut(issue.detail)
            if cut is not None:
                delta = _set_boundary_end(
                    boundaries, idx, cut - 0.02, allow_earlier=True
                )
                max_delta = max(max_delta, delta)
                if idx + 1 < len(boundaries):
                    delta = _set_boundary_start(
                        boundaries, idx + 1, cut, allow_earlier=True
                    )
                    max_delta = max(max_delta, delta)
            continue

        if issue.code in ("handoff_landmarks_disagree", "cut_in_speech"):
            cut = parse_slice_global_cut(issue.detail)
            if cut is not None:
                delta = _set_boundary_end(
                    boundaries, idx, cut, allow_earlier=True
                )
                max_delta = max(max_delta, delta)
                if idx + 1 < len(boundaries):
                    delta = _set_boundary_start(
                        boundaries, idx + 1, cut, allow_earlier=True
                    )
                    max_delta = max(max_delta, delta)
            continue

    return max_delta


def apply_slice_boundary_corrections(
    boundaries: list[Boundary],
    issues: list[ExportBoundaryIssue],
    segments: tuple[Segment, ...],
    words: list[WordTiming],
    *,
    min_score: float,
    pass_num: int,
) -> float:
    """Apply only post-export slice inspection corrections (after script handoffs)."""
    return apply_boundary_corrections(
        boundaries,
        issues,
        segments,
        words,
        min_score=min_score,
        pass_num=pass_num,
        codes=_SLICE_ONLY_CODES,
    )
