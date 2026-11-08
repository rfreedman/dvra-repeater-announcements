from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG

from app.config import LOG_DIR, LOG_KEEP_DAYS

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
LOG_FILENAME = "announcements.log"
ACCESS_LOG_FILENAME = "access.log"

# Exact GET paths omitted from access.log (home polls, health, SSE).
_ACCESS_NOISE_GET_PATHS = frozenset(
    {
        "/api/ready",
        "/api/health",
        "/api/system/stats",
        "/api/events/home",
        "/api/events/stats",
        "/api/schedules",
        "/api/announcements",
    }
)


class AccessNoiseFilter(logging.Filter):
    """Drop high-frequency status polls from uvicorn access records."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        method = str(args[1]).upper()
        path = str(args[2]).split("?", 1)[0]
        if method == "GET" and path in _ACCESS_NOISE_GET_PATHS:
            return False
        return True


def serve_log_config(log_dir: Path | None = None) -> dict:
    """Uvicorn dictConfig: app logs on stderr + announcements.log; filtered access.log only."""
    directory = Path(log_dir or LOG_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    app_path = directory / LOG_FILENAME
    access_path = directory / ACCESS_LOG_FILENAME
    config = deepcopy(LOGGING_CONFIG)
    config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    config["formatters"]["default"]["datefmt"] = LOG_DATEFMT
    config["formatters"]["access"]["fmt"] = (
        '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    config["formatters"]["access"]["datefmt"] = LOG_DATEFMT
    # Plain access lines for the file (AccessFormatter colors when use_colors is unset).
    config["formatters"]["access_file"] = {
        "()": "uvicorn.logging.AccessFormatter",
        "fmt": (
            '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
        ),
        "datefmt": LOG_DATEFMT,
        "use_colors": False,
    }
    config["formatters"]["file"] = {
        "format": LOG_FORMAT,
        "datefmt": LOG_DATEFMT,
    }
    config["formatters"]["console"] = {
        "format": LOG_FORMAT,
        "datefmt": LOG_DATEFMT,
    }
    config["filters"] = {
        "access_noise": {
            "()": "app.logging_setup.AccessNoiseFilter",
        }
    }
    config["handlers"]["file"] = {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "formatter": "file",
        "filename": str(app_path),
        "when": "midnight",
        "backupCount": LOG_KEEP_DAYS,
        "encoding": "utf-8",
    }
    config["handlers"]["access_file"] = {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "formatter": "access_file",
        "filename": str(access_path),
        "when": "midnight",
        "backupCount": LOG_KEEP_DAYS,
        "encoding": "utf-8",
        "filters": ["access_noise"],
    }
    config["handlers"]["console"] = {
        "class": "logging.StreamHandler",
        "formatter": "console",
        "stream": "ext://sys.stderr",
    }
    # Drop uvicorn's default stdout access handler; access goes only to access_file.
    config["handlers"].pop("access", None)
    config["loggers"]["uvicorn"]["handlers"] = ["default", "file"]
    config["loggers"]["uvicorn.access"] = {
        "handlers": ["access_file"],
        "level": "INFO",
        "propagate": False,
    }
    # Per-job add/remove INFO lines; app.scheduler emits one sync summary instead.
    config["loggers"]["apscheduler"] = {"level": "WARNING"}
    config["root"] = {"handlers": ["console", "file"], "level": "INFO"}
    return config
