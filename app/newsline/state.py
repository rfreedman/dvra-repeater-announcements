"""Last Newsline download, recorded only after a successful split."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import NEWSLINE_DIR
from app.store import write_json_atomic


class NewsState(BaseModel):
    report_number: int | None = None
    split_ok: bool = False
    completed_at: datetime | None = None
    error: str | None = None
    phase: str = "idle"
    last_check_at: datetime | None = None
    # Report number being downloaded or split. Cleared when the check goes idle.
    working_report: int | None = None
    # "new" after a split, "current" when a check finds that report already split.
    last_outcome: str | None = None

    def stories_dir(self, root: Path | None = None) -> Path | None:
        if self.report_number is None or not self.split_ok:
            return None
        return (root or NEWSLINE_DIR) / str(self.report_number) / "stories"


def state_path(root: Path | None = None) -> Path:
    return (root or NEWSLINE_DIR) / "state.json"


def load_state(root: Path | None = None) -> NewsState:
    path = state_path(root)
    if not path.exists():
        return NewsState()
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return NewsState()
    return NewsState.model_validate_json(text)


def save_state(state: NewsState, root: Path | None = None) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, state.model_dump(mode="json"))
