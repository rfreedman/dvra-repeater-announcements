from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.newsline.engine.models import Segment, SegmentKind
from app.newsline.engine.script_phrases import speakable_line_token_lists

_BREAK_HEAD_PATTERNS = (
    "time for you to identify your station",
    "identify your station",
    "heard on bulletin stations around the world",
    "we are the amateur radio newsline heard",
)

_OPENER_HEAD_TOKENS = 28


def first_line_opener_score(segment: Segment, spoken: list[str]) -> float | None:
    lines = speakable_line_token_lists(segment)
    if not lines or segment.kind not in (SegmentKind.STORY, SegmentKind.CLOSE):
        return None
    head = " ".join(spoken[:_OPENER_HEAD_TOKENS])
    if not head.strip():
        return 0.0
    # Best of the first few speakable lines — secondary headlines / kickers
    # can still appear ahead of the real ANCHOR opener in odd scripts.
    best = 0.0
    for line in lines[:3]:
        sample = " ".join(line[: min(12, len(line))])
        if not sample:
            continue
        best = max(best, float(fuzz.token_set_ratio(sample, head)))
    return best


def break_boilerplate_in_head(spoken: list[str]) -> str | None:
    head = " ".join(spoken[:_OPENER_HEAD_TOKENS])
    head = re.sub(r"\s+", " ", head)
    for pattern in _BREAK_HEAD_PATTERNS:
        if pattern in head:
            return pattern
    return None


def next_story_opener_inside(
    spoken: list[str], next_segment: Segment
) -> tuple[float, str]:
    lines = speakable_line_token_lists(next_segment)
    if not lines:
        return 0.0, ""
    sample = " ".join(lines[0][: min(10, len(lines[0]))])
    if len(spoken) <= 16:
        return 0.0, sample
    # Bleed shows up in the last third of the slice, not from common words like "in".
    tail_region = spoken[max(0, (len(spoken) * 2) // 3) :]
    tail_str = " ".join(tail_region)
    score = float(fuzz.token_set_ratio(sample, tail_str))
    return score, sample

