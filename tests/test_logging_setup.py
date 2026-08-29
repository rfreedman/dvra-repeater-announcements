from __future__ import annotations

import logging
import logging.config
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.logging_setup import LOG_FILENAME, serve_log_config

_LOGGER_NAMES = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "app.fire")


def _snapshot() -> dict[str, tuple]:
    out = {}
    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        out[name] = (logger.level, list(logger.handlers), logger.propagate)
    return out


def _restore(state: dict[str, tuple]) -> None:
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


def test_serve_log_config_writes_app_logs_not_access(tmp_path: Path):
    config = serve_log_config(tmp_path)
    assert config["handlers"]["file"]["when"] == "midnight"
    assert config["handlers"]["file"]["backupCount"] == 30
    assert config["loggers"]["uvicorn.access"]["handlers"] == ["access"]
    assert "file" in config["loggers"]["uvicorn"]["handlers"]
    assert "file" in config["root"]["handlers"]

    previous = _snapshot()
    logging.config.dictConfig(config)
    try:
        logging.getLogger("app.fire").info("Fire transmitted; On-the-hour")
        logging.getLogger("uvicorn.access").info(
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
        text = (tmp_path / LOG_FILENAME).read_text()
        assert "Fire transmitted; On-the-hour" in text
        assert "app.fire" in text
        assert "/api/schedules" not in text
        rotating = [h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)]
        assert rotating
        assert rotating[0].backupCount == 30
        assert rotating[0].when == "MIDNIGHT"
        access_rotating = [
            h
            for h in logging.getLogger("uvicorn.access").handlers
            if isinstance(h, TimedRotatingFileHandler)
        ]
        assert access_rotating == []
    finally:
        _restore(previous)
