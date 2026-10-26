from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.newsline.engine.models import Segment, SegmentKind, WordTiming
from app.newsline.engine.transcript import _line_is_spoken, strip_speaker_tag

_MIN_SEGMENT_SEC = 5.0
_BILLBOARD_MIN_SEC = 14.0
_START_LEAD_SEC = 0.35
_POST_BILLBOARD_LEAD_SEC = 0.05
_SIGNOFF_PAD_SEC = 0.12
_DEFAULT_ANCHOR_WORDS = 12
_SHORT_ANCHOR_WORDS = 6
_MIN_ANCHOR_WORDS = 6
_WORDS_PER_SECOND = 2.5


def estimated_min_duration(segment: Segment) -> float:
    if segment.kind == SegmentKind.BILLBOARD:
        return _BILLBOARD_MIN_SEC
    word_count = len(normalize_tokens(segment.speakable_text))
    return max(_MIN_SEGMENT_SEC, word_count / _WORDS_PER_SECOND)


def normalize_token(text: str) -> str:
    text = text.lower()
    text = text.replace("'", "'")
    text = re.sub(r"[^a-z0-9'/]+", "", text)
    return text


def normalize_tokens(text: str) -> list[str]:
    raw = re.sub(r"[^\w\s'/]", " ", text.lower())
    tokens = [normalize_token(t) for t in raw.split() if normalize_token(t)]
    return tokens


def _flatten_words(
    words: list[WordTiming],
) -> tuple[list[str], list[int]]:
    """Flat token stream; each index maps back to a Whisper word index."""
    tokens: list[str] = []
    word_for_token: list[int] = []
    for wi, w in enumerate(words):
        for tok in normalize_tokens(w.word):
            tokens.append(tok)
            word_for_token.append(wi)
    return tokens, word_for_token


def anchor_phrases(segment: Segment) -> list[list[str]]:
    tokens = normalize_tokens(segment.speakable_text)
    phrases: list[list[str]] = []
    spoken_lines = 0
    for line in segment.body_lines:
        line_tokens = normalize_tokens(_clean_line_for_anchor(line))
        if not line_tokens:
            continue
        phrases.append(line_tokens[: min(len(line_tokens), _DEFAULT_ANCHOR_WORDS)])
        if len(line_tokens) >= _SHORT_ANCHOR_WORDS:
            phrases.append(line_tokens[:_SHORT_ANCHOR_WORDS])
        spoken_lines += 1
        if spoken_lines >= 3:
            break
    if segment.kind == SegmentKind.CLOSE and tokens:
        for prefix_len in (14, 10, 8):
            if len(tokens) >= prefix_len:
                phrases.append(tokens[:prefix_len])
    if tokens:
        phrases.append(tokens[:_DEFAULT_ANCHOR_WORDS])
        if len(tokens) >= _SHORT_ANCHOR_WORDS:
            phrases.append(tokens[:_SHORT_ANCHOR_WORDS])
    seen: set[tuple[str, ...]] = set()
    unique: list[list[str]] = []
    for phrase in phrases:
        key = tuple(phrase)
        if key and key not in seen:
            seen.add(key)
            unique.append(phrase)
    return unique


def _clean_line_for_anchor(line: str) -> str:
    text = strip_speaker_tag(line)
    text = re.sub(r"\[PRON:[^\]]+\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[DO NOT READ:[^\]]+\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[(?:quote|endquote)\]", "", text, flags=re.IGNORECASE)
    return text


def _match_score(anchor: list[str], window: list[str]) -> float:
    if not anchor or not window:
        return 0.0
    anchor_str = " ".join(anchor)
    window_str = " ".join(window)
    if len(anchor) <= _SHORT_ANCHOR_WORDS:
        return float(fuzz.ratio(anchor_str, window_str))
    return float(fuzz.token_set_ratio(anchor_str, window_str))


def _stream_index_at_time(
    words: list[WordTiming],
    word_for_token: list[int],
    t: float,
) -> int:
    for i, wi in enumerate(word_for_token):
        if words[wi].start >= t:
            return max(0, i)
    return max(0, len(word_for_token) - 1)


def _time_for_token_index(
    words: list[WordTiming],
    word_for_token: list[int],
    token_idx: int,
    *,
    lead_sec: float = 0.0,
) -> float:
    wi = word_for_token[token_idx]
    t = words[wi].start - lead_sec
    if token_idx > 0 and word_for_token[token_idx - 1] != wi:
        prev_wi = word_for_token[token_idx - 1]
        gap = words[wi].start - words[prev_wi].end
        # Only split intra-phrase pauses; long gaps are another story or dead air.
        if gap < 0.85:
            gap_mid = (words[prev_wi].end + words[wi].start) / 2
            t = min(t, gap_mid)
    return max(0.0, t)


def find_boundary_start(
    anchor_phrase: list[str],
    words: list[WordTiming],
    tokens: list[str],
    word_for_token: list[int],
    search_from: int,
    *,
    min_score: float = 55.0,
    not_before_time: float = 0.0,
    start_lead_sec: float = _START_LEAD_SEC,
) -> tuple[float | None, float, str, int]:
    if not anchor_phrase or search_from >= len(tokens):
        return None, 0.0, "", search_from

    while search_from < len(tokens) and words[word_for_token[search_from]].start < not_before_time:
        search_from += 1

    width = len(anchor_phrase)
    if search_from + width > len(tokens):
        return None, 0.0, "", search_from

    best_score = 0.0
    best_idx = search_from
    snippet = " ".join(anchor_phrase[: min(8, len(anchor_phrase))])

    for i in range(search_from, len(tokens) - width + 1):
        if words[word_for_token[i]].start < not_before_time:
            continue
        window = tokens[i : i + width]
        score = _match_score(anchor_phrase, window)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score < min_score:
        return None, best_score, snippet, search_from

    # Walk back to the first token of the opening phrase when ASR merged words.
    back = min(3, best_idx)
    while back > 0 and best_idx - back >= search_from:
        trial = tokens[best_idx - back : best_idx - back + width]
        if _match_score(anchor_phrase, trial) >= best_score - 3:
            best_idx -= back
            break
        back -= 1

    start_time = _time_for_token_index(
        words, word_for_token, best_idx, lead_sec=start_lead_sec
    )
    return start_time, best_score, snippet, best_idx


def _time_after_phrase(
    anchor_phrase: list[str],
    words: list[WordTiming],
    tokens: list[str],
    word_for_token: list[int],
    search_from: int,
    *,
    min_score: float = 55.0,
    not_before_time: float = 0.0,
) -> tuple[float | None, float, int]:
    start, score, _snippet, idx = find_boundary_start(
        anchor_phrase,
        words,
        tokens,
        word_for_token,
        search_from,
        min_score=min_score,
        not_before_time=not_before_time,
        start_lead_sec=0.0,
    )
    if start is None:
        return None, score, search_from
    end_idx = idx + len(anchor_phrase) - 1
    if end_idx >= len(word_for_token):
        return None, score, search_from
    wi = word_for_token[end_idx]
    return words[wi].end, score, idx


def _signoff_phrases(segment: Segment) -> list[list[str]]:
    """
    Suffixes of the segment's last *speakable* script line.

    Uses ``_line_is_spoken`` so parenthetical credit lines are skipped.
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
        if not _line_is_spoken(line):
            continue
        tokens = normalize_tokens(_clean_line_for_anchor(line))
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


def _last_signoff_end_in_range(
    words: list[WordTiming],
    t_start: float,
    t_end: float,
    segment: Segment | None = None,
) -> float | None:
    """
    End time of the latest script-derived sign-off match in ``[t_start, t_end]``.

    Matches last-line suffixes from ``segment``'s transcript against Whisper words.
    """
    if segment is None or not words:
        return None
    phrases = _signoff_phrases(segment)
    if not phrases:
        return None
    tokens, word_for_token = _flatten_words(words)
    best_end: float | None = None
    best_score = -1.0
    for phrase in phrases:
        end_t, score, _idx = _time_after_phrase(
            phrase,
            words,
            tokens,
            word_for_token,
            0,
            min_score=55.0,
            not_before_time=t_start,
        )
        if end_t is None:
            continue
        if end_t < t_start - 0.05 or end_t > t_end + 0.75:
            continue
        if best_end is None or end_t > best_end + 0.02 or (
            abs(end_t - best_end) <= 0.02 and score > best_score
        ):
            best_end = end_t
            best_score = score
    return best_end


def _opening_speech_time(
    segment: Segment,
    words: list[WordTiming],
    tokens: list[str],
    word_for_token: list[int],
    not_before: float,
    *,
    min_score: float,
) -> float | None:
    """Whisper word time for the best opening anchor (avoids weak in-story false positives)."""
    search_from = _stream_index_at_time(words, word_for_token, not_before)
    candidates: list[tuple[float, float]] = []
    for phrase in anchor_phrases(segment):
        _start, score, _snippet, idx = find_boundary_start(
            phrase,
            words,
            tokens,
            word_for_token,
            search_from,
            min_score=min_score,
            not_before_time=not_before,
            start_lead_sec=0.0,
        )
        if score < min_score:
            continue
        wi = word_for_token[idx]
        candidates.append((words[wi].start, score))
    if not candidates:
        return None
    best_score = max(s for _t, s in candidates)
    tier = [c for c in candidates if c[1] >= best_score - 5.0]
    tier.sort(key=lambda c: (-c[1], c[0]))
    return tier[0][0]


