from __future__ import annotations

from app.models import SLOT_MINUTES, is_slot_stamp
from app.slots import SLOT_MINUTES as SLOTS_MODULE_MINUTES


def test_slot_minutes_shared_between_models_and_slots():
    assert SLOTS_MODULE_MINUTES is SLOT_MINUTES
    assert SLOT_MINUTES == (0, 30)


def test_is_slot_stamp_accepts_hour_and_half_hour_only():
    assert is_slot_stamp("00:00")
    assert is_slot_stamp("12:30")
    assert not is_slot_stamp("12:15")
    assert not is_slot_stamp("12:45")
