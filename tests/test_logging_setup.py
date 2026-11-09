from __future__ import annotations

import logging
import logging.config
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import cast

from app.logging_setup import (
    ACCESS_LOG_FILENAME,
    AccessNoiseFilter,
    LOG_FILENAME,
    serve_log_config,
)

_LOGGER_NAMES = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "app.fire")
_LoggerState = tuple[int, list[logging.Handler], bool]


def _snapshot() -> dict[str, _LoggerState]:
    out: dict[str, _LoggerState] = {}
    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        out[name] = (logger.level, list(logger.handlers), logger.propagate)
    return out


def _restore(state: dict[str, _LoggerState]) -> None:
    seen: set[int] = set()
    for name, (level, handlers, propagate) in state.items():
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            if id(handler) not in {id(item) for item in handlers} and id(handler) not in seen:
                handler.close()
                seen.add(id(handler))
        logger.setLevel(level)
        logger.propagate = propagate
        for handler in handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)


def _access_record(method: str, path: str) -> logging.LogRecord:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1", method, path, "1.1", 200),
        exc_info=None,
    )
    return record


def test_access_noise_filter_matrix() -> None:
    filt = AccessNoiseFilter()
    assert filt.filter(_access_record("GET", "/api/schedules")) is False
    assert filt.filter(_access_record("GET", "/api/schedules?clock_date=2026-11-08")) is False
    assert filt.filter(_access_record("GET", "/api/announcements")) is False
    assert filt.filter(_access_record("GET", "/api/ready")) is False
    assert filt.filter(_access_record("GET", "/api/health")) is False
    assert filt.filter(_access_record("GET", "/api/system/stats")) is False
    assert filt.filter(_access_record("GET", "/api/events/home")) is False
    assert filt.filter(_access_record("GET", "/api/events/stats")) is False
    assert filt.filter(_access_record("GET", "/")) is True
    assert filt.filter(_access_record("GET", "/static/app.js")) is True
    assert filt.filter(_access_record("GET", "/api/schedules/abc")) is True
    assert filt.filter(_access_record("GET", "/api/announcements/abc")) is True
    assert filt.filter(_access_record("GET", "/api/voices")) is True
    assert filt.filter(_access_record("POST", "/api/schedules/abc/trigger")) is True
    assert filt.filter(_access_record("POST", "/api/speak")) is True


def test_serve_log_config_splits_app_and_access(tmp_path: Path) -> None:
    config = serve_log_config(tmp_path)
    handlers = cast(dict[str, dict[str, object]], config["handlers"])
    loggers = cast(dict[str, dict[str, object]], config["loggers"])
    root = cast(dict[str, object], config["root"])
    formatters = cast(dict[str, dict[str, object]], config["formatters"])
    assert handlers["file"]["when"] == "midnight"
    assert handlers["file"]["backupCount"] == 30
    assert handlers["access_file"]["when"] == "midnight"
    assert handlers["access_file"]["backupCount"] == 30
    assert loggers["uvicorn.access"]["handlers"] == ["access_file"]
    assert loggers["apscheduler"]["level"] == "WARNING"
    assert "access" not in handlers
    assert "file" in cast(list[str], loggers["uvicorn"]["handlers"])
    assert "file" in cast(list[str], root["handlers"])

    previous = _snapshot()
    logging.config.dictConfig(config)
    try:
        logging.getLogger("app.fire").info("Fire transmitted; trigger=scheduled; On-the-hour")
        access = logging.getLogger("uvicorn.access")
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1",
            "GET",
            "/",
            "1.1",
            200,
        )
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1",
            "GET",
            "/api/schedules",
            "1.1",
            200,
        )
        for name in _LOGGER_NAMES:
            for handler in logging.getLogger(name).handlers:
                handler.flush()
        app_text = (tmp_path / LOG_FILENAME).read_text()
        access_text = (tmp_path / ACCESS_LOG_FILENAME).read_text()
        assert "Fire transmitted; trigger=scheduled; On-the-hour" in app_text
        assert "app.fire" in app_text
        assert "/api/schedules" not in app_text
        assert 'GET / HTTP/1.1' in access_text or 'GET / ' in access_text
        assert "/api/schedules" not in access_text
        assert "Fire transmitted" not in access_text
        assert "\x1b" not in access_text
        assert formatters["access_file"]["use_colors"] is False
        rotating = [h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)]
        assert rotating
        assert rotating[0].backupCount == 30
        assert rotating[0].when == "MIDNIGHT"
        access_handlers = logging.getLogger("uvicorn.access").handlers
        assert any(isinstance(h, TimedRotatingFileHandler) for h in access_handlers)
        # TimedRotatingFileHandler subclasses StreamHandler; require no console-only handler.
        assert not any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, TimedRotatingFileHandler)
            for h in access_handlers
        )
    finally:
        _restore(previous)
