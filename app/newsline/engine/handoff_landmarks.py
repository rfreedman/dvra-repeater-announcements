"""Match script sign-off and opener phrases in a handoff word stream."""

from __future__ import annotations

from rapidfuzz import fuzz

from app.newsline.engine.align import flatten_words
from app.newsline.engine.models import Segment, WordTiming
from app.newsline.engine.script_phrases import (
    normalize_tokens,
    signoff_phrases,
    speakable_line_token_lists,
)

_MIN_MATCH = 86.0
_MIN_MATCH_LOOSE = 82.0


def _orderedmatch_score(anchor: list[str], window: list[str]) -> float:
    """Ordered string ratio only — token_set_ratio false-matches shared little words."""
    if not anchor or not window or len(anchor) != len(window):
        return 0.0
    return float(fuzz.ratio(" ".join(anchor), " ".join(window)))


def _opener_phrase(segment: Segment) -> list[str]:
    lines = speakable_line_token_lists(segment)
    if not lines:
        return normalize_tokens(segment.speakable_text)[:8]
    return lines[0][: min(10, len(lines[0]))]


def _signoff_candidates(segment: Segment) -> list[list[str]]:
    """Sign-off / intro-tail phrases from the segment's own script only."""
    return signoff_phrases(segment)


def _opener_candidates(segment: Segment) -> list[list[str]]:
    base = _opener_phrase(segment)
    out: list[list[str]] = []
    if len(base) >= 4:
        out.append(base)
        out.append(base[:6] if len(base) > 6 else base)
        out.append(base[:4])
        # Beep trains often garble the first word(s); match the stable tail.
        if len(base) >= 6:
            out.append(base[1:7])
            out.append(base[2:8] if len(base) >= 8 else base[2:])
    elif base:
        out.append(base)
    return out


def _find_phrase_span(
    phrase: list[str],
    words: list[WordTiming],
    *,
    not_before: float | None = None,
    not_after: float | None = None,
    prefer: str = "best",
    min_match: float = _MIN_MATCH,
) -> tuple[float, float, float] | None:
    """Return ``(start, end, score)`` for the best phrase match in ``words``."""
    if not phrase or not words:
        return None
    tokens, owners = flatten_words(words)
    width = len(phrase)
    if width > len(tokens):
        return None
    candidates: list[tuple[float, float, float, int]] = []
    for i in range(len(tokens) - width + 1):
        w0 = words[owners[i]]
        if not_before is not None and w0.start < not_before:
            continue
        if not_after is not None and w0.start > not_after:
            continue
        window = tokens[i : i + width]
        # Require the first token to be a strong match (blocks callsign↔opener swaps).
        if fuzz.ratio(phrase[0], window[0]) < 80:
            continue
        score = _orderedmatch_score(phrase, window)
        if score < min_match:
            continue
        w1 = words[owners[i + width - 1]]
        candidates.append((w0.start, w1.end, score, i))
    if not candidates:
        return None
    if prefer == "latest":
        candidates.sort(key=lambda c: (c[1], c[2]))
        start, end, score, _ = candidates[-1]
    elif prefer == "earliest":
        candidates.sort(key=lambda c: (c[0], -c[2]))
        start, end, score, _ = candidates[0]
    else:
        candidates.sort(key=lambda c: (c[2], c[1]))
        start, end, score, _ = candidates[-1]
    return start, end, score


def best_signoff(
    words: list[WordTiming],
    segment: Segment,
    *,
    not_after: float | None = None,
) -> tuple[float, float, float] | None:
    best: tuple[float, float, float] | None = None
    for phrase in _signoff_candidates(segment):
        loose = len(phrase) <= 5
        hit = _find_phrase_span(
            phrase,
            words,
            not_after=not_after,
            prefer="latest",
            min_match=_MIN_MATCH_LOOSE if loose else _MIN_MATCH,
        )
        if hit is None:
            continue
        if best is None or hit[1] > best[1] or (
            abs(hit[1] - best[1]) < 0.05 and hit[2] > best[2]
        ):
            best = hit
    return best


def best_opener(
    words: list[WordTiming],
    segment: Segment,
    *,
    not_before: float | None = None,
) -> tuple[float, float, float] | None:
    best: tuple[float, float, float] | None = None
    for phrase in _opener_candidates(segment):
        hit = _find_phrase_span(
            phrase,
            words,
            not_before=not_before,
            prefer="earliest",
            min_match=_MIN_MATCH_LOOSE if len(phrase) <= 4 else _MIN_MATCH,
        )
        if hit is None:
            continue
        if best is None or hit[0] < best[0] or (
            abs(hit[0] - best[0]) < 0.05 and hit[2] > best[2]
        ):
            best = hit
    return best

