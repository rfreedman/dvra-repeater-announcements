from __future__ import annotations

import re
from pathlib import Path

from app.newsline.engine.models import Boundary, Segment, SegmentKind

_DELIMITER = re.compile(r"^\*\*\s*$")
_SOURCE_LINE = re.compile(r"^\([^)]+\)\s*$")
_PRON = re.compile(r"\[PRON:[^\]]+\]", re.IGNORECASE)
_DO_NOT_READ = re.compile(r"\[DO NOT READ:[^\]]+\]", re.IGNORECASE)
_QUOTE_TAG = re.compile(r"\[(?:quote|endquote)\]", re.IGNORECASE)
_REPORT_FROM_AUDIO = re.compile(r"Report(\d+)", re.IGNORECASE)
_REPORT_FROM_TXT = re.compile(r"nsln(\d+)", re.IGNORECASE)
# SKEETER/ANCHOR:, TRAVIS:, JIM DAMRON:, etc.
_SPEAKER_TAG = re.compile(
    r"^\s*(?:[A-Z][A-Z0-9Ø/.-]*(?:/[A-Z]+)?|[A-Z]+(?: [A-Z]+)*)\s*:\s*",
)


def extract_report_id_from_audio(path: Path) -> int | None:
    match = _REPORT_FROM_AUDIO.search(path.name)
    return int(match.group(1)) if match else None


def extract_report_id_from_transcript(path: Path) -> int | None:
    match = _REPORT_FROM_TXT.search(path.name)
    return int(match.group(1)) if match else None


# Scripts sometimes use ``BREAK  HERE`` (extra spaces) — match flexibly.
_BREAK_HERE_RE = re.compile(r"^BREAK\s+HERE\b", re.IGNORECASE)
_BREAK_HERE_PREFIX_RE = re.compile(r"^BREAK\s+HERE\s*:\s*", re.IGNORECASE)


def is_break_here_text(text: str) -> bool:
    """True when ``text`` is a BREAK HERE title/line (any internal spacing)."""
    return bool(_BREAK_HERE_RE.match(text.strip()))


def strip_break_prefix(title: str) -> str:
    text = title.strip()
    match = _BREAK_HERE_PREFIX_RE.match(text)
    if match:
        return text[match.end() :].strip()
    return text


def strip_kicker_prefix(title: str) -> str:
    text = title.strip()
    if text.upper().startswith("KICKER:"):
        return text[7:].strip()
    return text


def classify_segment_title(title: str) -> SegmentKind:
    stripped = title.strip()
    upper = stripped.upper()
    if upper == "BILLBOARD CART":
        return SegmentKind.BILLBOARD
    if is_break_here_text(stripped):
        return SegmentKind.BREAK
    if upper == "NEWSCAST CLOSE":
        return SegmentKind.CLOSE
    return SegmentKind.STORY


def _line_is_headline_label(line: str) -> bool:
    """
    Unspoken ALL-CAPS secondary headline (no speaker tag).

    e.g. NSLN 2552: ``IN CUBA, A RADIO PIONEER'S PRESENCE SENDS A STRONG SIGNAL``
    between the kicker title and ``PAUL/ANCHOR: …``.
    """
    if _SPEAKER_TAG.match(line):
        return False
    stripped = line.strip()
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) < 8:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return (upper / len(letters)) >= 0.85


def _line_is_spoken(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _SOURCE_LINE.match(stripped):
        return False
    if _DO_NOT_READ.search(stripped):
        return False
    return not _line_is_headline_label(stripped)


def strip_speaker_tag(line: str) -> str:
    return _SPEAKER_TAG.sub("", line)


def _clean_spoken_line(line: str) -> str:
    text = strip_speaker_tag(line)
    text = _PRON.sub("", text)
    text = _DO_NOT_READ.sub("", text)
    text = _QUOTE_TAG.sub("", text)
    return text.strip()


def _speakable_for_segment(title: str, body_lines: tuple[str, ...], kind: SegmentKind) -> str:
    """Story titles after ``**`` are labels only; alignment uses body text."""
    if kind == SegmentKind.BREAK:
        spoken = strip_break_prefix(title)
        if spoken:
            return speakable_from_lines((spoken,))
    return speakable_from_lines(body_lines)


def speakable_from_lines(lines: tuple[str, ...]) -> str:
    parts: list[str] = []
    for line in lines:
        if not _line_is_spoken(line):
            continue
        cleaned = _clean_spoken_line(line)
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts)


def _is_break_here_line(line: str) -> bool:
    return is_break_here_text(line)


def _segment(
    index: int,
    title: str,
    body_lines: tuple[str, ...],
    kind: SegmentKind,
) -> Segment:
    return Segment(
        index=index,
        title=title,
        body_lines=body_lines,
        kind=kind,
        speakable_text=_speakable_for_segment(title, body_lines, kind),
    )


def _split_inline_break(
    title: str,
    body_lines: tuple[str, ...],
    kind: SegmentKind,
) -> list[tuple[str, tuple[str, ...], SegmentKind]]:
    """
    Some scripts put ``BREAK HERE:…`` inside the previous story body instead of
    as its own ``**``-titled block (e.g. NSLN 2553). Peel that into a BREAK.
    """
    if kind != SegmentKind.STORY:
        return [(title, body_lines, kind)]
    break_at: int | None = None
    for i, line in enumerate(body_lines):
        if _is_break_here_line(line):
            break_at = i
            break
    if break_at is None:
        return [(title, body_lines, kind)]

    before = list(body_lines[:break_at])
    while before and not before[-1].strip():
        before.pop()
    break_title = body_lines[break_at].strip()
    after = list(body_lines[break_at + 1 :])
    while after and not after[0].strip():
        after.pop(0)

    out: list[tuple[str, tuple[str, ...], SegmentKind]] = [
        (title, tuple(before), SegmentKind.STORY),
        (break_title, tuple(after), SegmentKind.BREAK),
    ]
    return out


def parse_transcript(text: str) -> tuple[Segment, ...]:
    lines = text.splitlines()
    segments: list[Segment] = []
    intro_lines: list[str] = []
    i = 0
    while i < len(lines) and not _DELIMITER.match(lines[i]):
        intro_lines.append(lines[i])
        i += 1

    if intro_lines and any(line.strip() for line in intro_lines):
        intro_body = tuple(intro_lines)
        segments.append(
            _segment(0, "INTRO", intro_body, SegmentKind.INTRO)
        )

    block_index = 0
    while i < len(lines):
        if not _DELIMITER.match(lines[i]):
            i += 1
            continue
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        raw_title = lines[i].strip()
        i += 1
        body: list[str] = []
        while i < len(lines) and not _DELIMITER.match(lines[i]):
            body.append(lines[i])
            i += 1
        body_tuple = tuple(body)
        kind = classify_segment_title(raw_title)
        title = strip_kicker_prefix(raw_title)
        for part_title, part_body, part_kind in _split_inline_break(
            title, body_tuple, kind
        ):
            block_index += 1
            segments.append(_segment(block_index, part_title, part_body, part_kind))

    return tuple(segments)


def load_transcript(path: Path) -> tuple[Segment, ...]:
    return parse_transcript(path.read_text(encoding="utf-8"))


_EXPORT_KINDS = frozenset(
    {SegmentKind.INTRO, SegmentKind.BILLBOARD, SegmentKind.STORY, SegmentKind.CLOSE}
)


def export_boundaries(
    boundaries: tuple[Boundary, ...],
    segments: tuple[Segment, ...],
    audio_duration: float,
    *,
    end_trim_sec: float = 0.15,
) -> tuple[Boundary, ...]:
    """Keep aligned start/end times (including ``BREAK`` blocks between stories)."""
    del audio_duration, end_trim_sec  # breaks define story edges; trim applied in align only
    kind_by_index = {s.index: s.kind for s in segments}
    return tuple(b for b in boundaries if kind_by_index.get(b.segment_index) in _EXPORT_KINDS)


def break_follows_segment(segments: tuple[Segment, ...], segment_index: int) -> bool:
    for i, seg in enumerate(segments):
        if seg.index == segment_index:
            if i + 1 < len(segments):
                return segments[i + 1].kind == SegmentKind.BREAK
            return False
    return False
