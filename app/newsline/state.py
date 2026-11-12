"""Last Newsline download, recorded only after a successful split."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

from app.config import NEWSLINE_DIR
from app.json_io import write_json_atomic

NewslinePhase = Literal["idle", "checking", "downloading", "splitting"]
NewslineOutcome = Literal["new", "current"]


class NewsState(BaseModel):
    report_number: int | None = None
    # Friday the script names as the intended air date, not the download day.
    release_friday: date | None = None
    # RSS pubDate in the station timezone. A naive value is a date saved before the time was kept.
    published_on: datetime | None = None

    @field_validator("published_on", mode="before")
    @classmethod
    def published_instant(cls, value: object) -> object:
        if isinstance(value, str) and len(value) == 10:
            return datetime.fromisoformat(value)
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime(value.year, value.month, value.day)
        return value
    split_ok: bool = False
    completed_at: datetime | None = None
    error: str | None = None
    phase: NewslinePhase = "idle"
    last_check_at: datetime | None = None
    # Report number being downloaded or split. Cleared when the check goes idle.
    working_report: int | None = None
    # "new" after a split, "current" when a check finds that report already split.
    last_outcome: NewslineOutcome | None = None
    # Downloaded this report but split failed; scheduled checks skip it.
    split_failed_report: int | None = None

    def stories_dir(self, root: Path | None = None) -> Path | None:
        if self.report_number is None or not self.split_ok:
            return None
        return (root or NEWSLINE_DIR) / str(self.report_number) / "stories"


def published_on_text(value: datetime | None) -> str | None:
    """ISO text for the status line. A date-only value has no clock."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.date().isoformat()
    return value.isoformat()


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
