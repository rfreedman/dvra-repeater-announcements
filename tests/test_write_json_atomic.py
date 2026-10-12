from __future__ import annotations

import json
from pathlib import Path

from app.store import write_json_atomic


def test_write_json_atomic_writes_formatted_json(tmp_path: Path):
    path = tmp_path / "data" / "doc.json"
    data = {"version": 2, "items": []}
    write_json_atomic(path, data)
    assert path.read_text(encoding="utf-8") == json.dumps(data, indent=2) + "\n"
    assert not path.with_suffix(".tmp").exists()


def test_write_json_atomic_creates_parent_dirs(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "users.json"
    write_json_atomic(path, {"version": 1, "users": []})
    assert path.is_file()
