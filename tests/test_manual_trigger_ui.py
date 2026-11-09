"""Browser coverage for manual-trigger countdown copy and the background poll (issue #25).

Opt-in: ``uv run playwright install chromium`` then ``uv run pytest -m e2e``.
"""

from __future__ import annotations
from app.radio import StubRadio

import re
import socket
import threading
import time
from collections.abc import Iterable, Iterator
from typing import cast

import pytest
import uvicorn
from playwright.sync_api import Page, Request, Route, expect

from app.audio import PcmChunk
from app.models import Announcement, Schedule
from app.live_notify import notify_home_live_status
from app.manual_trigger_state import clear_manual_inflight, set_manual_inflight
from app.server import app
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME
from tests.test_api import seed_baseline

from app.store import AnnouncementStore
from app.users import User
pytestmark = pytest.mark.e2e


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        address = cast(tuple[str, int], sock.getsockname())
        return address[1]


@pytest.fixture
def live_server(page: Page, store: AnnouncementStore, admin_user: User, radio: StubRadio, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Schedule]]:
    """In-process app. Playback blocks so a Trigger now POST stays open."""
    del admin_user, radio
    release = threading.Event()
    monkeypatch.setattr("app.config.TRIGGER_NOW", True)

    def no_op() -> None:
        return

    def no_sleep(_seconds: float) -> None:
        return

    def chunks_for(_item: Announcement) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    monkeypatch.setattr("app.server.start_scheduler", no_op)
    monkeypatch.setattr("app.scheduler_fire.time.sleep", no_sleep)
    monkeypatch.setattr(
        "app.scheduler_fire.chunks_for_announcement",
        chunks_for,
    )

    def play(_chunks: Iterable[PcmChunk]) -> None:
        _ = release.wait(30)

    monkeypatch.setattr("app.scheduler_fire.play_chunks", play)
    _announcement, schedule = seed_baseline(store)

    port = _free_port()
    server = _Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, name="e2e-uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("e2e server did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", schedule
    finally:
        release.set()
        page.close()
        server.should_exit = True
        thread.join(10)


def _login(page: Page, base_url: str) -> None:
    _ = page.goto(base_url)
    page.locator("#login-username").wait_for(state="visible", timeout=15_000)
    page.locator("#login-username").fill(ADMIN_USERNAME)
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-submit").click()
    page.locator("#view-home.active").wait_for(timeout=15_000)
    page.locator("[data-trigger-now]").wait_for(timeout=15_000)


def test_trigger_in_flight_shows_running(page: Page, live_server: tuple[str, Schedule]) -> None:
    base_url, _schedule = live_server
    _login(page, base_url)
    page.locator("[data-trigger-now]").click()
    expect(page.locator("#next-run-status")).to_have_text("Running", timeout=5_000)
    expect(page.locator("#next-run-countdown")).to_have_text(re.compile(r"^(Now|\d+:\d{2})$"))
    expect(page.locator("[data-trigger-now]")).to_have_text("Playing")


def test_trigger_polls_with_background_header_when_sse_is_down(page: Page, live_server: tuple[str, Schedule]) -> None:
    base_url, _schedule = live_server

    def reject_sse(route: Route) -> None:
        route.fulfill(status=503, body="unavailable")

    def is_background_poll(req: Request) -> bool:
        return (
            req.method == "GET"
            and "/api/schedules" in req.url
            and "x-background-poll" in {key.lower() for key in req.headers}
        )

    _ = page.route("**/api/events/home", reject_sse)
    _login(page, base_url)
    with page.expect_request(
        is_background_poll,
        timeout=5_000,
    ):
        page.locator("[data-trigger-now]").click()


def test_clear_hold_countdown_then_starting_now(page: Page, live_server: tuple[str, Schedule], monkeypatch: pytest.MonkeyPatch) -> None:
    base_url, schedule = live_server
    hold = {"idle": 3.0}
    def radio_status() -> tuple[bool, float, float]:
        return False, hold["idle"], 10.0

    monkeypatch.setattr("app.manual_trigger_state.radio_status_for_ui", radio_status)
    _login(page, base_url)
    set_manual_inflight(schedule.id, "Station ID")
    try:
        expect(page.locator("#next-run-status")).to_have_text("Clear to transmit", timeout=5_000)
        expect(page.locator("#next-run-countdown")).to_contain_text("remaining")
        hold["idle"] = 10.0
        notify_home_live_status(force=True)
        expect(page.locator("#next-run-countdown")).to_have_text("Starting now…", timeout=5_000)
        expect(page.locator("[data-trigger-now]")).to_have_text("Starting…")
    finally:
        clear_manual_inflight(schedule.id)
        notify_home_live_status(force=True)
