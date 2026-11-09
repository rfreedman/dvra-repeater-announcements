"""Script phrase helpers shared by alignment, handoff cuts, and slice QA.

Token normalization, sign-off suffixes, and speakable line tokens are the
public phrase API. Callers should import them from here rather than reaching
into ``align`` or ``verify_fulltext``.
"""

from __future__ import annotations

import re

from app.newsline.engine.models import Segment, SegmentKind
from app.newsline.engine.transcript import (
    clean_spoken_line,
    line_is_spoken,
    strip_break_prefix,
)


def normalize_token(text: str) -> str:
    text = text.lower()
    text = text.replace("'", "'")
    text = re.sub(r"[^a-z0-9'/]+", "", text)
    return text


def normalize_tokens(text: str) -> list[str]:
    raw = re.sub(r"[^\w\s'/]", " ", text.lower())
    tokens = [normalize_token(t) for t in raw.split() if normalize_token(t)]
    return tokens


def signoff_phrases(segment: Segment) -> list[list[str]]:
    """
    Suffixes of the segment's last *speakable* script line.

    Uses ``line_is_spoken`` so parenthetical credit lines are skipped.
    Length and skip-first-token variants help ASR wording drift (still the
    same ``.txt`` line — no stock phrases).
    """
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def _add(phrase: list[str]) -> None:
        key = tuple(phrase)
        if len(phrase) >= 3 and key not in seen:
            seen.add(key)
            out.append(phrase)

    def _add_suffixes(tokens: list[str]) -> None:
        for n in (8, 6, 5, 4, 3):
            if len(tokens) >= n:
                _add(tokens[-n:])
        if len(tokens) > 8:
            _add(tokens[-min(10, len(tokens)) :])

    if segment.kind == SegmentKind.INTRO:
        toks = normalize_tokens(segment.speakable_text)
        _add_suffixes(toks)
        for skip in (1, 2):
            if len(toks) > skip + 2:
                _add_suffixes(toks[skip:])
        return out

    last_tokens: list[str] | None = None
    for line in reversed(segment.body_lines):
        if not line_is_spoken(line):
            continue
        tokens = normalize_tokens(clean_spoken_line(line))
        if len(tokens) >= 3:
            last_tokens = tokens
            break
    if last_tokens is None:
        return out

    _add_suffixes(last_tokens)
    # ASR often rewords the first word(s) of a sign-off line; still the same
    # script tokens after the skip.
    for skip in (1, 2):
        if len(last_tokens) > skip + 2:
            _add_suffixes(last_tokens[skip:])
    return out


def speakable_line_token_lists(segment: Segment) -> list[list[str]]:
    """Token lists for each speakable script line, in order."""
    lines: list[list[str]] = []
    if segment.kind == SegmentKind.BREAK:
        text = strip_break_prefix(segment.title)
        toks = normalize_tokens(text)
        if len(toks) >= 3:
            lines.append(toks)
        return lines
    for line in segment.body_lines:
        if not line_is_spoken(line):
            continue
        cleaned = clean_spoken_line(line)
        toks = normalize_tokens(cleaned)
        if len(toks) >= 3:
            lines.append(toks)
    return lines
