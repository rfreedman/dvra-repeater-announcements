"""Schedule model validation (priority defaults, shared helpers)."""

import pytest
from pydantic import ValidationError

from app.models import DEFAULT_PRIORITY, Announcement, Schedule, normalize_entity_name


def test_normalize_entity_name_strips_and_requires():
    assert normalize_entity_name("  Net  ") == "Net"
    with pytest.raises(ValueError, match="Name is required"):
        normalize_entity_name("   ")


def test_announcement_name_uses_shared_normalizer():
    with pytest.raises(ValidationError):
        Announcement(name="  ", text="Hello.")


def test_emergency_schedule_priority_always_default():
    row = Schedule.model_validate(
        {
            "name": "Skywarn",
            "kind": "emergency",
            "announcement_id": "a1",
            "priority": 999,
        }
    )
    assert row.priority == DEFAULT_PRIORITY["emergency"]
