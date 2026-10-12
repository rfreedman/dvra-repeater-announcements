"""Home live status SSE (issue #27)."""

from __future__ import annotations

import asyncio
import json

from app.home_live import build_home_live_status, notify_home_data_changed, notify_home_live_status
from app.scheduler import _clear_running, _set_running
from app.sse_hub import (
    format_sse,
    publish_home_data_changed,
    publish_home_live_status,
    set_event_loop,
    subscribe,
    unsubscribe,
)
from tests.test_api import _seed_baseline


def test_home_sse_requires_auth(store, admin_user):
    from fastapi.testclient import TestClient

    from app.server import app

    with TestClient(app) as anon:
        res = anon.get("/api/events/home")
        assert res.status_code == 401


def test_format_sse_roundtrip():
    payload = {"now": "2026-01-01T12:00:00-05:00", "upcoming": None}
    raw = format_sse("snapshot", payload)
    assert raw.startswith("event: snapshot\n")
    data_line = next(line for line in raw.split("\n") if line.startswith("data:"))
    assert json.loads(data_line[5:].strip()) == payload


def test_force_publish_bypasses_min_interval(store):
    _seed_baseline(store)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        queue = subscribe()
        try:
            publish_home_live_status(force=True)
            await asyncio.sleep(0)
            await asyncio.wait_for(queue.get(), timeout=2.0)
            publish_home_live_status(force=True)
            await asyncio.sleep(0)
            second = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert second.event == "status"
            assert "now" in second.data
        finally:
            unsubscribe(queue)

    asyncio.run(run())


def test_sse_hub_delivers_publish(store):
    _seed_baseline(store)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        queue = subscribe()
        try:
            await asyncio.sleep(0.06)
            publish_home_live_status()
            await asyncio.sleep(0)
            message = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert message.event == "status"
            assert "now" in message.data
            assert "pending_manual_triggers" in message.data
        finally:
            unsubscribe(queue)

    asyncio.run(run())


def test_data_changed_event(store):
    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        queue = subscribe()
        try:
            publish_home_data_changed()
            await asyncio.sleep(0)
            message = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert message.event == "data_changed"
            assert message.data == {}
        finally:
            unsubscribe(queue)

    asyncio.run(run())


def test_create_schedule_notifies_data_changed(client, store, monkeypatch):
    _seed_baseline(store)
    calls: list[str] = []

    def _record() -> None:
        calls.append("data_changed")

    monkeypatch.setattr("app.server.notify_home_data_changed", _record)
    res = client.post(
        "/api/schedules",
        json={
            "name": "Extra net",
            "kind": "weekly",
            "announcement_id": store.list_announcements()[0].id,
            "days": ["mon"],
            "slots": ["20:00"],
        },
    )
    assert res.status_code == 200
    assert calls == ["data_changed"]


def test_build_home_live_status_reflects_running(store):
    announcement, schedule = _seed_baseline(store)
    try:
        _set_running(announcement, schedule.id)
        live = build_home_live_status()
        assert live["upcoming"]["status"] == "running"
        assert live["upcoming"]["schedule_id"] == schedule.id
    finally:
        _clear_running()


def test_build_home_live_status_matches_schedules_keys(client, store):
    _seed_baseline(store)
    live = build_home_live_status()
    home = client.get("/api/schedules").json()
    assert live["upcoming"] == home["upcoming"]
    assert live["pending_manual_triggers"] == home["pending_manual_triggers"]
    assert live["manual_trigger_drop_notice"] == home["manual_trigger_drop_notice"]


def test_notify_home_live_status_no_subscribers():
    notify_home_live_status()


def test_notify_home_data_changed_no_subscribers():
    notify_home_data_changed()
