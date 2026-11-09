"""Versioned JSON read/write used by the announcement store, users, and Newsline state.

Kept as a leaf so Newsline state does not import ``app.store`` (that closed an
import cycle through ``schedule_play`` → ``playlist``). Keep that playlist import inside the function.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

JsonObject = dict[str, object]


class DataFileError(RuntimeError):
    """A data file exists but is not in a format this version understands."""


def read_versioned_json(path: Path, *, version: int, kind: str) -> JsonObject | None:
    """Parsed JSON object, or None if the file is missing/empty.

    Raises instead of returning an empty document for an unknown format: the
    next save would otherwise overwrite the operator's data.
    """
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    raw = cast(object, json.loads(text))
    if raw == {}:
        return None
    if not isinstance(raw, dict):
        raise DataFileError(f"{path} is not a {kind} JSON object; refusing to overwrite it.")
    entries = cast(JsonObject, raw)
    if entries.get("version") != version:
        found = entries.get("version")
        raise DataFileError(
            f"{path} is not a version {version} {kind} file (found version {found!r}). "
            + "Move it aside or restore a backup; refusing to overwrite it."
        )
    return entries


def write_json_atomic(path: Path, data: Mapping[str, object]) -> None:
    """Write JSON atomically via a same-directory .tmp file and replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    tmp = path.with_suffix(".tmp")
    _ = tmp.write_text(payload, encoding="utf-8")
    _ = tmp.replace(path)
