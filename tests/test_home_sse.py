"""Home live status SSE (issue #27)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.home_live import build_home_live_status
from app.live_notify import notify_home_data_changed, notify_home_live_status
from app.scheduler import clear_running, set_running
from app.store import AnnouncementStore
from app.users import User
from app.sse_hub import (
    clear_shutdown_state,
    format_sse,
    publish_home_data_changed,
    publish_home_live_status,
    set_event_loop,
    shutdown_sse_streams,
    subscribe,
    unsubscribe,
)
from tests.test_api import seed_baseline
from tests.jsonutil import json_dict


def test_home_sse_requires_auth(store: AnnouncementStore, admin_user: User) -> None:
    _ = store
    _ = admin_user
    from fastapi.testclient import TestClient

    from app.server import app

    with TestClient(app) as anon:
        res = anon.get("/api/events/home")
        assert res.status_code == 401


def test_format_sse_roundtrip() -> None:
    payload = {"now": "2026-01-01T12:00:00-05:00", "upcoming": None}
    raw = format_sse("snapshot", payload)
    assert raw.startswith("event: snapshot\n")
    data_line = next(line for line in raw.split("\n") if line.startswith("data:"))
    assert json.loads(data_line[5:].strip()) == payload


def test_force_publish_bypasses_min_interval(store: AnnouncementStore) -> None:
    _ = seed_baseline(store)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        queue = subscribe()
        try:
            publish_home_live_status(force=True)
            await asyncio.sleep(0)
            _ = await asyncio.wait_for(queue.get(), timeout=2.0)
            publish_home_live_status(force=True)
            await asyncio.sleep(0)
            second = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert second is not None
            assert second.event == "status"
            assert "now" in second.data
        finally:
            unsubscribe(queue)

    asyncio.run(run())


def test_sse_hub_delivers_publish(store: AnnouncementStore) -> None:
    _ = seed_baseline(store)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        queue = subscribe()
        try:
            await asyncio.sleep(0.06)
            publish_home_live_status()
            await asyncio.sleep(0)
            message = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert message is not None
            assert message.event == "status"
            assert "now" in message.data
            assert "pending_manual_triggers" in message.data
        finally:
            unsubscribe(queue)

    asyncio.run(run())


def test_data_changed_event(store: AnnouncementStore) -> None:
    _ = store
    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        queue = subscribe()
        try:
            publish_home_data_changed()
            await asyncio.sleep(0)
            message = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert message is not None
            assert message.event == "data_changed"
            assert message.data == {}
        finally:
            unsubscribe(queue)

    asyncio.run(run())


def test_create_schedule_notifies_data_changed(client: TestClient, store: AnnouncementStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = seed_baseline(store)
    calls: list[str] = []

    def _record() -> None:
        calls.append("data_changed")

    monkeypatch.setattr("app.routes.schedules.notify_home_data_changed", _record)
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


def test_build_home_live_status_reflects_running(store: AnnouncementStore) -> None:
    announcement, schedule = seed_baseline(store)
    try:
        set_running(announcement, schedule.id)
        live = build_home_live_status()
        upcoming = live["upcoming"]
        assert upcoming is not None
        assert upcoming["status"] == "running"
        assert upcoming["schedule_id"] == schedule.id
        assert upcoming["phase"] == "playing"
        assert live["newsline_playing"] is False
    finally:
        clear_running()


def test_build_home_live_status_reflects_running_wait_phase(store: AnnouncementStore) -> None:
    from app.scheduler import set_running_wait

    announcement, schedule = seed_baseline(store)
    try:
        set_running(announcement, schedule.id)
        set_running_wait(
            phase="channel_busy",
            clear_seconds=2.0,
            clear_remaining_seconds=None,
            force=True,
        )
        live = build_home_live_status()
        upcoming = live["upcoming"]
        assert upcoming is not None
        assert upcoming["status"] == "running"
        assert upcoming["phase"] == "channel_busy"
        assert upcoming["clear_seconds"] == 2.0
        assert upcoming["clear_remaining_seconds"] is None
    finally:
        clear_running()


def test_build_home_live_status_reflects_newsline_bulletin(store: AnnouncementStore) -> None:
    from app.newsline.session import bulletin_session

    _ = seed_baseline(store)
    bulletin_session.begin()
    try:
        live = build_home_live_status()
        assert live["newsline_playing"] is True
    finally:
        bulletin_session.end()
    assert build_home_live_status()["newsline_playing"] is False


def test_build_home_live_status_matches_schedules_keys(client: TestClient, store: AnnouncementStore) -> None:
    _ = seed_baseline(store)
    live = build_home_live_status()
    home = json_dict(client.get("/api/schedules"))
    assert live["upcoming"] == home["upcoming"]
    assert live["pending_manual_triggers"] == home["pending_manual_triggers"]
    assert live["manual_trigger_drop_notice"] == home["manual_trigger_drop_notice"]


def test_notify_home_live_status_no_subscribers() -> None:
    notify_home_live_status()


def test_notify_home_data_changed_no_subscribers() -> None:
    notify_home_data_changed()


def test_shutdown_sse_streams_unblocks_subscribers() -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        set_event_loop(loop)
        clear_shutdown_state()
        queue = subscribe()
        try:
            shutdown_sse_streams()
            await asyncio.sleep(0)
            payload = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert payload is None
        finally:
            unsubscribe(queue)
            clear_shutdown_state()

    asyncio.run(run())
