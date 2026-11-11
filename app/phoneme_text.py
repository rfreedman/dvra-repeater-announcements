"""Apply the phoneme dictionary to announcement text before Piper sees it.

``[[phonemes]]`` stays a phoneme block. ``{{phrase}}`` is spoken as written and
is not dictionary-matched. A spelling wrapped in asterisks, such as
``*440-frequency*``, is a shortcut and may contain punctuation. A dictionary
row is either bare IPA, which this module wraps in ``[[ ]]``, or alternate
plain text, which is inserted as written.
The tie mark (U+200D) stays in saved IPA and is removed from phoneme blocks
here, because Piper has no id for it and would only skip it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# Same pause tags as app.audio._PAUSE_TAG. prepare_speech_text keeps them intact
# so a later parse_script still sees the pause.
_PAUSE_TAG = re.compile(r"\[pause:\s*(\d+(?:\.\d+)?)\s*s?\]", re.IGNORECASE)
# Letters or digits, with an apostrophe or hyphen only inside the word.
# "Delaware's" and "mega-hertz" are each one token.
_TOKEN = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
_MARKUP = ("[[", "]]", "{{", "}}")
# espeak --ipa=3 tie mark. Piper's phoneme map has no id for it.
_TIE_MARK = "\u200d"


class PhonemeTextError(ValueError):
    """A dictionary spelling or IPA string cannot be stored."""


@dataclass(frozen=True)
class PhonemeReplacement:
    """One dictionary row. ``tokens`` are casefolded whole words.

    ``ipa`` and ``alternate`` are mutually exclusive. Exactly one is non-empty.
    ``shortcut`` is the spelling including its asterisks when this row is a
    ``*shortcut*`` marker rather than whole words.
    """

    spelling: str
    ipa: str
    tokens: tuple[str, ...]
    alternate: str = ""
    shortcut: str = ""


def compile_entry(spelling: str, ipa: str, alternate: str = "") -> PhonemeReplacement:
    """Normalize one row. Reject empty values, markup, and punctuation spellings."""
    collapsed = " ".join(spelling.split())
    if not collapsed:
        raise PhonemeTextError("Spelling is required")
    if any(mark in collapsed for mark in _MARKUP):
        raise PhonemeTextError("Spelling cannot contain [[ ]] or {{ }}")
    shortcut = _shortcut_spelling(collapsed)
    pieces: list[re.Match[str]]
    if shortcut is not None:
        pieces = []
        display = shortcut
    else:
        pieces = list(_TOKEN.finditer(collapsed))
        display = " ".join(piece.group() for piece in pieces)
        if not display or display.casefold() != collapsed.casefold():
            raise PhonemeTextError("Spelling must be a word or phrase without punctuation")
    cleaned_ipa = ipa.strip()
    cleaned_alternate = " ".join(alternate.split())
    if cleaned_ipa and cleaned_alternate:
        raise PhonemeTextError("Enter IPA or alternate text, not both")
    if not cleaned_ipa and not cleaned_alternate:
        raise PhonemeTextError("Enter IPA or alternate text")
    if any(mark in cleaned_ipa for mark in _MARKUP):
        raise PhonemeTextError("IPA cannot contain [[ ]] or {{ }}")
    if any(mark in cleaned_alternate for mark in _MARKUP):
        raise PhonemeTextError("Alternate text cannot contain [[ ]] or {{ }}")
    tokens = tuple(piece.group().casefold() for piece in pieces)
    return PhonemeReplacement(
        spelling=display,
        ipa=cleaned_ipa,
        tokens=tokens,
        alternate=cleaned_alternate,
        shortcut=shortcut or "",
    )


def compile_entries(
    rows: Sequence[tuple[str, str] | tuple[str, str, str]],
) -> tuple[PhonemeReplacement, ...]:
    """Compile every row. A case-insensitive duplicate spelling is an error."""
    compiled: list[PhonemeReplacement] = []
    seen: dict[str, str] = {}
    for row in rows:
        if len(row) == 2:
            spelling, ipa = row
            alternate = ""
        else:
            spelling, ipa, alternate = row
        entry = compile_entry(spelling, ipa, alternate)
        key = entry.shortcut.casefold() if entry.shortcut else " ".join(entry.tokens)
        previous = seen.get(key)
        if previous is not None:
            raise PhonemeTextError(f"{entry.spelling} duplicates {previous}")
        seen[key] = entry.spelling
        compiled.append(entry)
    return tuple(compiled)


def prepare_speech_text(
    text: str,
    entries: Sequence[PhonemeReplacement],
    *,
    use_dictionary: bool,
) -> str:
    """Return text for Piper. Braces are always removed. The dictionary is optional.

    Tie marks are removed from ``[[ ]]`` blocks. Saved IPA is not changed.
    """
    active = _ranked(entries) if use_dictionary else ()
    parts: list[str] = []
    last = 0
    for match in _PAUSE_TAG.finditer(text):
        parts.append(_apply_markup(text[last : match.start()], active))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_apply_markup(text[last:], active))
    return "".join(parts)


def matching_entries(
    text: str,
    entries: Sequence[PhonemeReplacement],
    *,
    use_dictionary: bool,
) -> tuple[PhonemeReplacement, ...]:
    """Entries that would replace a whole word or phrase in ``text``, longest first."""
    if not use_dictionary:
        return ()
    ranked = _ranked(entries)
    found: list[PhonemeReplacement] = []
    seen: set[str] = set()
    for plain in _plain_runs(text):
        for entry in _walk(plain, ranked):
            key = entry.shortcut.casefold() if entry.shortcut else " ".join(entry.tokens)
            if key in seen:
                continue
            seen.add(key)
            found.append(entry)
    return tuple(found)


def _ranked(entries: Sequence[PhonemeReplacement]) -> tuple[PhonemeReplacement, ...]:
    return tuple(sorted(entries, key=lambda entry: (len(entry.tokens), len(entry.spelling)), reverse=True))


def _plain_runs(text: str) -> list[str]:
    """Text outside pause tags, manual phonemes, and double-brace spans."""
    runs: list[str] = []
    last = 0
    for match in _PAUSE_TAG.finditer(text):
        runs.extend(_plain_pieces(text[last : match.start()]))
        last = match.end()
    runs.extend(_plain_pieces(text[last:]))
    return runs


def _next_span(text: str, pos: int) -> tuple[int, int, str] | None:
    """Next complete ``[[ ]]`` or ``{{ }}`` span, or an unclosed opener through the end.

    An unclosed opener returns a negative end so the caller keeps the tail as typed.
    """
    bracket = text.find("[[", pos)
    brace = text.find("{{", pos)
    starts = [index for index in (bracket, brace) if index >= 0]
    if not starts:
        return None
    start = min(starts)
    opener = text[start : start + 2]
    closer = "]]" if opener == "[[" else "}}"
    end = text.find(closer, start + 2)
    if end < 0:
        return start, -1, opener
    return start, end + 2, opener


def _plain_pieces(text: str) -> list[str]:
    pieces: list[str] = []
    pos = 0
    while pos < len(text):
        found = _next_span(text, pos)
        if found is None:
            pieces.append(text[pos:])
            break
        start, end, _opener = found
        pieces.append(text[pos:start])
        if end < 0:
            break
        pos = end
    return pieces


def _apply_markup(text: str, entries: Sequence[PhonemeReplacement]) -> str:
    parts: list[str] = []
    pos = 0
    while pos < len(text):
        found = _next_span(text, pos)
        if found is None:
            parts.append(_replace_plain(text[pos:], entries))
            break
        start, end, opener = found
        parts.append(_replace_plain(text[pos:start], entries))
        if end < 0:
            parts.append(text[start:])
            break
        token = text[start:end]
        if opener == "{{":
            parts.append(token[2:-2])
        else:
            parts.append(_drop_tie_mark(token))
        pos = end
    return "".join(parts)


def _shortcut_spelling(collapsed: str) -> str | None:
    """Return ``*text*`` when ``collapsed`` is a shortcut. ``None`` means a normal spelling.

    An empty pair of asterisks is an error. A spelling with only one asterisk
    falls through to the normal punctuation check.
    """
    if len(collapsed) < 2 or not collapsed.startswith("*") or not collapsed.endswith("*"):
        return None
    inside = collapsed[1:-1].strip()
    if not inside:
        raise PhonemeTextError("Enter a shortcut between the asterisks")
    return f"*{inside}*"


def _replace_plain(text: str, entries: Sequence[PhonemeReplacement]) -> str:
    if not text or not entries:
        return text
    shortcuts = tuple(entry for entry in entries if entry.shortcut)
    if not shortcuts:
        return _replace_words(text, entries)
    pieces: list[str] = []
    pos = 0
    while pos < len(text):
        found = _next_shortcut(text, pos, shortcuts)
        if found is None:
            pieces.append(_replace_words(text[pos:], entries))
            break
        start, end, entry = found
        pieces.append(_replace_words(text[pos:start], entries))
        pieces.append(_spoken_form(entry))
        pos = end
    return "".join(pieces)


def _replace_words(text: str, entries: Sequence[PhonemeReplacement]) -> str:
    tokens = list(_TOKEN.finditer(text))
    if not tokens:
        return text
    pieces: list[str] = []
    pos = 0
    index = 0
    while index < len(tokens):
        found = _match_at(text, tokens, index, entries)
        if found is None:
            index += 1
            continue
        entry, count = found
        start = tokens[index].start()
        end = tokens[index + count - 1].end()
        pieces.append(text[pos:start])
        pieces.append(_spoken_form(entry))
        pos = end
        index += count
    pieces.append(text[pos:])
    return "".join(pieces)


def _walk(text: str, entries: Sequence[PhonemeReplacement]) -> list[PhonemeReplacement]:
    shortcuts = tuple(entry for entry in entries if entry.shortcut)
    hits: list[tuple[int, PhonemeReplacement]] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    while pos < len(text):
        found = _next_shortcut(text, pos, shortcuts)
        if found is None:
            break
        start, end, entry = found
        hits.append((start, entry))
        spans.append((start, end))
        pos = end
    tokens = list(_TOKEN.finditer(text))
    index = 0
    while index < len(tokens):
        if any(_overlaps(tokens[index], span) for span in spans):
            index += 1
            continue
        matched = _match_at(text, tokens, index, entries)
        if matched is None:
            index += 1
            continue
        entry, count = matched
        hits.append((tokens[index].start(), entry))
        index += count
    hits.sort(key=lambda item: item[0])
    return [entry for _, entry in hits]


def _next_shortcut(
    text: str,
    pos: int,
    shortcuts: Sequence[PhonemeReplacement],
) -> tuple[int, int, PhonemeReplacement] | None:
    """Earliest shortcut at or after ``pos``. The longest one wins a shared start."""
    best: tuple[int, int, PhonemeReplacement] | None = None
    for entry in shortcuts:
        found = re.search(re.escape(entry.shortcut), text[pos:], flags=re.IGNORECASE)
        if found is None:
            continue
        start = pos + found.start()
        end = pos + found.end()
        if best is None or start < best[0] or (start == best[0] and end > best[1]):
            best = (start, end, entry)
    return best


def _overlaps(token: re.Match[str], span: tuple[int, int]) -> bool:
    start, end = span
    return token.start() < end and token.end() > start


def _match_at(
    text: str,
    tokens: Sequence[re.Match[str]],
    index: int,
    entries: Sequence[PhonemeReplacement],
) -> tuple[PhonemeReplacement, int] | None:
    for entry in entries:
        count = len(entry.tokens)
        if count == 0 or index + count > len(tokens):
            continue
        window = tokens[index : index + count]
        if any(not _gap_is_space(text, window[pos], window[pos + 1]) for pos in range(count - 1)):
            continue
        heard = tuple(piece.group().casefold() for piece in window)
        if heard == entry.tokens:
            return entry, count
    return None


def _spoken_form(entry: PhonemeReplacement) -> str:
    """Text Piper should say for one dictionary row."""
    if entry.alternate:
        return entry.alternate
    return f"[[{_drop_tie_mark(entry.ipa)}]]"


def _drop_tie_mark(text: str) -> str:
    return text.replace(_TIE_MARK, "")


def _gap_is_space(text: str, left: re.Match[str], right: re.Match[str]) -> bool:
    return text[left.end() : right.start()].strip() == ""
