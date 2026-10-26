from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SegmentKind(StrEnum):
    INTRO = "intro"
    BILLBOARD = "billboard"
    STORY = "story"
    BREAK = "break"
    CLOSE = "close"


@dataclass(frozen=True)
class Segment:
    index: int
    title: str
    body_lines: tuple[str, ...]
    kind: SegmentKind
    speakable_text: str


@dataclass(frozen=True)
class WordTiming:
    word: str
    start: float
    end: float


@dataclass(frozen=True)
class Boundary:
    segment_index: int
    title: str
    start: float
    end: float
    match_score: float
    anchor_snippet: str
