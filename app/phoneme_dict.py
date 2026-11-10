"""Runtime phoneme dictionary. The file is operator data, not committed."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from app.config import PHONEMES_PATH
from app.json_io import DataFileError, read_versioned_json, write_json_atomic
from app.phoneme_text import PhonemeReplacement, PhonemeTextError, compile_entries

_lock = threading.Lock()


def _entry_payload(entries: tuple[PhonemeReplacement, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for entry in entries:
        row = {"spelling": entry.spelling, "ipa": entry.ipa}
        if entry.alternate:
            row["alternate"] = entry.alternate
        rows.append(row)
    return rows


class PhonemeDictionary:
    """Version 1 ``phonemes.json`` in the data directory."""

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path

    def compiled(self) -> tuple[PhonemeReplacement, ...]:
        with _lock:
            return self._read()

    def content_hash(self) -> str:
        """Stable hash of the saved rows. Empty when there are no entries."""
        payload = _entry_payload(self.compiled())
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def replace(
        self, rows: Sequence[tuple[str, str] | tuple[str, str, str]]
    ) -> tuple[PhonemeReplacement, ...]:
        """Replace the whole dictionary. Raises PhonemeTextError on a bad row."""
        entries = compile_entries(rows)
        with _lock:
            document: dict[str, object] = {"version": 1, "entries": _entry_payload(entries)}
            write_json_atomic(self.path, document)
            return entries

    def _read(self) -> tuple[PhonemeReplacement, ...]:
        raw = read_versioned_json(self.path, version=1, kind="phoneme dictionary")
        if raw is None:
            return ()
        items = raw.get("entries", [])
        if not isinstance(items, list):
            raise PhonemeTextError("Phoneme dictionary entries must be a list")
        rows: list[tuple[str, str, str]] = []
        for item in cast(list[object], items):
            if not isinstance(item, dict):
                raise PhonemeTextError("Phoneme dictionary entry must be an object")
            entry = cast(dict[object, object], item)
            spelling = entry.get("spelling", "")
            ipa = entry.get("ipa", "")
            alternate = entry.get("alternate", "")
            if (
                not isinstance(spelling, str)
                or not isinstance(ipa, str)
                or not isinstance(alternate, str)
            ):
                raise PhonemeTextError(
                    "Phoneme dictionary spelling, IPA, and alternate text must be text"
                )
            rows.append((spelling, ipa, alternate))
        try:
            return compile_entries(rows)
        except PhonemeTextError as exc:
            raise DataFileError(f"{self.path} has an invalid phoneme entry: {exc}") from exc


def get_phoneme_dictionary() -> PhonemeDictionary:
    from app.runtime import get_runtime

    runtime = get_runtime()
    current = runtime.phoneme_dictionary
    if isinstance(current, PhonemeDictionary):
        return current
    created = PhonemeDictionary(PHONEMES_PATH)
    runtime.phoneme_dictionary = created
    return created


def set_phoneme_dictionary(dictionary: PhonemeDictionary | None) -> None:
    from app.runtime import get_runtime

    get_runtime().phoneme_dictionary = dictionary
