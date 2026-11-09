"""Background startup gate (early HTML, API 503 until ready)."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from app.registry import EngineRegistry
from app.server import app
from tests.jsonutil import json_dict


import pytest
def test_ready_endpoint_when_sync_startup(client: TestClient) -> None:
    res = client.get("/api/ready")
    assert res.status_code == 200
    assert json_dict(res) == {"ready": True}


def test_api_returns_503_until_background_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()

    def slow_preload(self: EngineRegistry) -> list[str]:
        _ = self
        _ = gate.wait(timeout=10)
        return []

    def async_startup() -> bool:
        return False

    monkeypatch.setattr("app.registry.EngineRegistry.preload", slow_preload)
    monkeypatch.setattr("app.server._sync_startup_for_tests", async_startup)

    with TestClient(app) as test_client:
        assert test_client.get("/").status_code == 200
        assert json_dict(test_client.get("/api/ready")) == {"ready": False}
        blocked = test_client.get("/api/auth/status")
        assert blocked.status_code == 503
        assert json_dict(blocked)["detail"] == "starting"
        gate.set()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if json_dict(test_client.get("/api/ready")).get("ready"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("startup did not become ready")
        assert test_client.get("/api/auth/status").status_code == 200
