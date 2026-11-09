"""Schedule model validation (priority defaults, shared helpers)."""

import pytest
from pydantic import ValidationError

from app.models import (
    DEFAULT_PRIORITY,
    TtsAnnouncement,
    normalize_entity_name,
    validate_schedule,
)


def test_normalize_entity_name_strips_and_requires() -> None:
    assert normalize_entity_name("  Net  ") == "Net"
    with pytest.raises(ValueError, match="Name is required"):
        _ = normalize_entity_name("   ")


def test_announcement_name_uses_shared_normalizer() -> None:
    with pytest.raises(ValidationError):
        _ = TtsAnnouncement(name="  ", text="Hello.")


def test_emergency_schedule_priority_always_default() -> None:
    row = validate_schedule(
        {
            "name": "Skywarn",
            "kind": "emergency",
            "announcement_id": "a1",
            "priority": 999,
        }
    )
    assert row.priority == DEFAULT_PRIORITY["emergency"]


def test_old_flat_baseline_json_loads_and_saves_lean() -> None:
    row = validate_schedule(
        {
            "name": "Baseline ID",
            "kind": "baseline",
            "announcement_id": "a1",
            "days": [],
            "slots": [],
            "occurrence": None,
            "skip_months": [],
            "start_date": None,
            "event_date": None,
            "event_slot": None,
            "days_before": None,
        }
    )
    dumped = row.model_dump()
    assert dumped["kind"] == "baseline"
    assert "days" not in dumped
    assert "slots" not in dumped
    assert "occurrence" not in dumped
