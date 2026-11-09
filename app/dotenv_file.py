"""Update selected keys in a ``.env`` file without dropping comments or other keys."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def format_env_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Configuration values cannot contain line breaks")
    return value


def update_dotenv(path: Path, updates: Mapping[str, str]) -> None:
    """Replace ``updates`` keys in place. Unrelated lines, including comments, stay."""
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = {key: format_env_value(value) for key, value in updates.items()}
    lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines.append(f"{key}={remaining.pop(key)}")
        else:
            lines.append(line)
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")
