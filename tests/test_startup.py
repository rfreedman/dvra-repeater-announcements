from __future__ import annotations

import pytest

from app.startup import StartupError, init_radio_at_startup


def test_init_radio_at_startup_stub_is_noop(monkeypatch):
    monkeypatch.setattr("app.config.radio_backend", lambda: "stub")
    init_radio_at_startup()


def test_init_radio_at_startup_wraps_runtime_error(monkeypatch):
    monkeypatch.setattr("app.config.radio_backend", lambda: "dra")

    def boom():
        raise RuntimeError("No CM119A HID interface found.")

    monkeypatch.setattr("app.radio.create_radio_from_config", boom)
    with pytest.raises(StartupError, match="No CM119A HID"):
        init_radio_at_startup()
