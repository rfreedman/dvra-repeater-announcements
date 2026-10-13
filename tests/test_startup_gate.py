"""Background startup gate (early HTML, API 503 until ready)."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from app.server import app


def test_ready_endpoint_when_sync_startup(client):
    res = client.get("/api/ready")
    assert res.status_code == 200
    assert res.json() == {"ready": True}


def test_api_returns_503_until_background_startup(monkeypatch):
    gate = threading.Event()

    def slow_preload(self):
        gate.wait(timeout=10)
        return []

    monkeypatch.setattr("app.registry.EngineRegistry.preload", slow_preload)
    monkeypatch.setattr("app.server._sync_startup_for_tests", lambda: False)

    with TestClient(app) as test_client:
        assert test_client.get("/").status_code == 200
        assert test_client.get("/api/ready").json() == {"ready": False}
        blocked = test_client.get("/api/auth/status")
        assert blocked.status_code == 503
        assert blocked.json()["detail"] == "starting"
        gate.set()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if test_client.get("/api/ready").json().get("ready"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("startup did not become ready")
        assert test_client.get("/api/auth/status").status_code == 200
