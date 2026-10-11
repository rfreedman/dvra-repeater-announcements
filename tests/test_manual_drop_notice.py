"""Manual trigger give-up exposes a dismissible UI notice."""
from __future__ import annotations

from app.scheduler import (
    clear_manual_trigger_drop_notice,
    peek_manual_trigger_drop_notice,
    record_manual_trigger_drop,
    run_manual_trigger,
)
from tests.test_api import _seed_baseline


def test_record_and_peek_manual_drop_notice():
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


def test_run_manual_trigger_drop_records_notice(radio, store, monkeypatch):
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: None)
    _announcement, schedule = _seed_baseline(store)
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


def test_api_includes_drop_notice_and_dismiss(client, store, admin_user):
    record_manual_trigger_drop(
        "x",
        announcement_name="Test ID",
        reason="give_up",
    )
    payload = client.get("/api/schedules").json()
    assert payload["manual_trigger_drop_notice"] is not None
    assert payload["manual_trigger_drop_notice"]["announcement_name"] == "Test ID"
    res = client.post("/api/manual-trigger-drop/dismiss")
    assert res.status_code == 200
    payload = client.get("/api/schedules").json()
    assert payload["manual_trigger_drop_notice"] is None
    clear_manual_trigger_drop_notice()
