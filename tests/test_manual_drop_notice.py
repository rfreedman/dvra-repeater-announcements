"""Manual trigger give-up exposes a dismissible UI notice."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.fire_types import FireContext, FireDeps, FireOutcome
from app.manual_trigger_state import (
    clear_manual_trigger_drop_notice,
    peek_manual_trigger_drop_notice,
    record_manual_trigger_drop,
)
from app.radio import StubRadio
from app.scheduler import run_manual_trigger
from app.store import AnnouncementStore
from app.users import User
from tests.test_api import seed_baseline
from tests.jsonutil import json_dict


def test_usb_loss_drop_notice_detail() -> None:
    clear_manual_trigger_drop_notice()
    record_manual_trigger_drop(
        "sched-usb",
        announcement_name="Station ID",
        reason="usb_loss",
    )
    notice = peek_manual_trigger_drop_notice()
    assert notice is not None
    assert "unplugged" in str(notice["detail"]).lower()
    clear_manual_trigger_drop_notice()


def test_manual_trigger_usb_drop_maps_notice(monkeypatch: pytest.MonkeyPatch, radio: StubRadio, store: AnnouncementStore) -> None:
    _ = radio

    _announcement, schedule = seed_baseline(store)
    clear_manual_trigger_drop_notice()

    def drop_for_usb(_ctx: FireContext, _deps: FireDeps) -> FireOutcome:
        return FireOutcome.DROPPED_USB

    monkeypatch.setattr(
        "app.scheduler_fire.handle_fire",
        drop_for_usb,
    )
    result = run_manual_trigger(schedule.id)
    assert result == "dropped"
    notice = peek_manual_trigger_drop_notice()
    assert notice is not None
    assert notice["reason"] == "usb_loss"
    assert "unplugged" in str(notice["detail"]).lower()
    clear_manual_trigger_drop_notice()


def test_record_and_peek_manual_drop_notice() -> None:
    clear_manual_trigger_drop_notice()
    assert peek_manual_trigger_drop_notice() is None
    record_manual_trigger_drop(
        "sched-1",
        announcement_name="Station ID",
        reason="give_up",
    )
    notice = peek_manual_trigger_drop_notice()
    assert notice is not None
    assert notice["schedule_id"] == "sched-1"
    assert notice["announcement_name"] == "Station ID"
    assert "give-up" in str(notice["detail"]).lower() or "clear" in str(notice["detail"]).lower()
    clear_manual_trigger_drop_notice()
    assert peek_manual_trigger_drop_notice() is None


def test_run_manual_trigger_drop_records_notice(radio: StubRadio, store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_sleep(_seconds: float) -> None:
        return

    monkeypatch.setattr("app.scheduler_fire.time.sleep", no_sleep)
    _announcement, schedule = seed_baseline(store)
    radio.busy = True
    clear_manual_trigger_drop_notice()
    from datetime import datetime, timedelta

    from app.slots import zone_for

    zone = zone_for(store.document().settings.timezone)
    past = datetime.now(zone) - timedelta(seconds=60)
    deadline = datetime.now(zone) - timedelta(seconds=1)
    result = run_manual_trigger(
        schedule.id,
        fired_at_iso=past.isoformat(),
        deadline_iso=deadline.isoformat(),
    )
    assert result == "dropped"
    notice = peek_manual_trigger_drop_notice()
    assert notice is not None
    assert notice["schedule_id"] == schedule.id
    clear_manual_trigger_drop_notice()


def test_api_includes_drop_notice_and_dismiss(client: TestClient, store: AnnouncementStore, admin_user: User) -> None:
    _ = store
    _ = admin_user
    record_manual_trigger_drop(
        "x",
        announcement_name="Test ID",
        reason="give_up",
    )
    payload = json_dict(client.get("/api/schedules"))
    notice = payload["manual_trigger_drop_notice"]
    assert isinstance(notice, dict)
    assert notice["announcement_name"] == "Test ID"
    res = client.post("/api/manual-trigger-drop/dismiss")
    assert res.status_code == 200
    payload = json_dict(client.get("/api/schedules"))
    assert payload["manual_trigger_drop_notice"] is None
    clear_manual_trigger_drop_notice()
