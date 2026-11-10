"""Show Piper's missing-phoneme warning with a visible character and its word.

Piper logs ``Missing phoneme from id map:`` plus the phoneme. A tie mark is
U+200D ZERO WIDTH JOINER, so the character does not appear in the log.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import override

from app.json_io import DataFileError
from app.phoneme_text import PhonemeReplacement, PhonemeTextError

log = logging.getLogger(__name__)

_PIPER_LOGGER = "piper.phoneme_ids"
_PIPER_MESSAGE = "Missing phoneme from id map: %s"
# Same blocks Piper splits on. The inside is one phoneme per character.
_BLOCK = re.compile(r"\[\[(.*?)\]\]")
_TOKEN = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")

PhonemizeWord = Callable[[str], Sequence[str]]


@dataclass
class _SpeechPhonemes:
    text: str
    entries: tuple[PhonemeReplacement, ...]
    phonemize_word: PhonemizeWord | None
    seen: set[str] = field(default_factory=set)
    phone_cache: dict[str, tuple[str, ...]] = field(default_factory=dict)


_speech: ContextVar[_SpeechPhonemes | None] = ContextVar("missing_phoneme_speech", default=None)


def describe_phoneme(phoneme: str) -> str:
    """A phoneme that stays visible in a log line, including zero-width characters."""
    if not phoneme:
        return "empty phoneme"
    parts = [f"U+{ord(char):04X} {unicodedata.name(char, 'UNNAMED')}" for char in phoneme]
    shown = " ".join(parts)
    if all(_visible(char) for char in phoneme):
        return f"{phoneme} ({shown})"
    return shown


def install_missing_phoneme_filter() -> None:
    """Rewrite Piper's missing-phoneme warning. Safe to call more than once."""
    logger = logging.getLogger(_PIPER_LOGGER)
    if any(isinstance(item, MissingPhonemeLogFilter) for item in logger.filters):
        return
    logger.addFilter(MissingPhonemeLogFilter())


@contextmanager
def missing_phoneme_context(text: str, phonemize_word: PhonemizeWord | None) -> Generator[None, None, None]:
    """Attribute missing phonemes in ``text`` while Piper is synthesizing it."""
    state = _SpeechPhonemes(
        text=text,
        entries=_dictionary_entries(),
        phonemize_word=phonemize_word,
    )
    token = _speech.set(state)
    try:
        yield
    finally:
        _speech.reset(token)


class MissingPhonemeLogFilter(logging.Filter):
    """Replace an invisible phoneme and name the plaintext that produced it."""

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != _PIPER_LOGGER or record.msg != _PIPER_MESSAGE:
            return True
        phoneme = _phoneme_arg(record)
        if phoneme is None:
            return True
        try:
            state = _speech.get()
            if state is not None and phoneme in state.seen:
                return False
            plaintext = _plaintext_label(phoneme, state)
            if state is not None:
                state.seen.add(phoneme)
        except Exception:
            log.exception("Could not describe missing phoneme %r", phoneme)
            return True
        record.msg = "Missing phoneme from id map: %s; plaintext %s"
        record.args = (describe_phoneme(phoneme), plaintext)
        return True


def _phoneme_arg(record: logging.LogRecord) -> str | None:
    args = record.args
    if isinstance(args, tuple) and len(args) == 1 and isinstance(args[0], str):
        return args[0]
    return None


def _plaintext_label(phoneme: str, state: _SpeechPhonemes | None) -> str:
    if state is None:
        return "unknown"
    words: list[str] = []
    unknown_ipa: list[str] = []
    in_block = False
    for match in _BLOCK.finditer(state.text):
        ipa = match.group(1).strip()
        if phoneme not in set(ipa):
            continue
        in_block = True
        spellings = [entry.spelling for entry in state.entries if entry.ipa == ipa]
        if spellings:
            words.extend(spellings)
        else:
            unknown_ipa.append(ipa)
    if not in_block and state.phonemize_word is not None:
        for word in _plain_words(state.text):
            if phoneme in _phones(state, word):
                words.append(word)
    return _format_plaintext(words, unknown_ipa, state.text, searched_plain=not in_block)


def _phones(state: _SpeechPhonemes, word: str) -> tuple[str, ...]:
    cached = state.phone_cache.get(word)
    if cached is not None:
        return cached
    phonemize_word = state.phonemize_word
    if phonemize_word is None:
        return ()
    try:
        phones = tuple(phonemize_word(word))
    except Exception:
        log.exception("Could not phonemize %r while reporting a missing phoneme", word)
        phones = ()
    state.phone_cache[word] = phones
    return phones


def _plain_words(text: str) -> list[str]:
    words: list[str] = []
    pos = 0
    for match in _BLOCK.finditer(text):
        words.extend(token.group() for token in _TOKEN.finditer(text[pos : match.start()]))
        pos = match.end()
    words.extend(token.group() for token in _TOKEN.finditer(text[pos:]))
    return words


def _format_plaintext(
    words: list[str],
    unknown_ipa: list[str],
    text: str,
    *,
    searched_plain: bool,
) -> str:
    parts: list[str] = []
    unique_words = list(dict.fromkeys(words))
    if unique_words:
        parts.append(", ".join(_quote(word) for word in unique_words))
    unique_ipa = list(dict.fromkeys(unknown_ipa))
    if unique_ipa:
        shown = ", ".join(ascii(ipa) for ipa in unique_ipa)
        parts.append(f"unknown for phonemes {shown}")
    if parts:
        return "; ".join(parts)
    if searched_plain and text:
        return f"unknown in {ascii(text)}"
    return "unknown"


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _visible(char: str) -> bool:
    return char.isprintable() and not unicodedata.category(char).startswith("C")


def _dictionary_entries() -> tuple[PhonemeReplacement, ...]:
    from app.phoneme_dict import get_phoneme_dictionary

    try:
        return get_phoneme_dictionary().compiled()
    except (DataFileError, PhonemeTextError):
        return ()


install_missing_phoneme_filter()
