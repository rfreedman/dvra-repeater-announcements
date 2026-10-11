from __future__ import annotations

import logging

from app.fire_trace import fire_trace


def test_fire_trace_no_op_when_disabled(monkeypatch, caplog):
    monkeypatch.setattr("app.config.FIRE_VERBOSE_LOG", False)
    with caplog.at_level(logging.INFO):
        fire_trace("should not appear %s", 1)
    assert not [r for r in caplog.records if r.name == "app.fire.trace"]


def test_fire_trace_logs_when_enabled(monkeypatch, caplog):
    monkeypatch.setattr("app.config.FIRE_VERBOSE_LOG", True)
    monkeypatch.setattr("app.fire_trace.fire_verbose_log_enabled", lambda: True)
    with caplog.at_level(logging.INFO):
        fire_trace("verbose %s", "ok")
    assert any("verbose ok" in r.message for r in caplog.records if r.name == "app.fire.trace")
