from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG

from app.config import LOG_DIR, LOG_KEEP_DAYS

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
LOG_FILENAME = "announcements.log"


def serve_log_config(log_dir: Path | None = None) -> dict:
    """Uvicorn dictConfig: console unchanged, plus a daily file excluding access logs."""
    directory = Path(log_dir or LOG_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_FILENAME
    config = deepcopy(LOGGING_CONFIG)
    config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    config["formatters"]["default"]["datefmt"] = LOG_DATEFMT
    config["formatters"]["access"]["fmt"] = (
        '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    config["formatters"]["access"]["datefmt"] = LOG_DATEFMT
    config["formatters"]["file"] = {
        "format": LOG_FORMAT,
        "datefmt": LOG_DATEFMT,
    }
    config["formatters"]["console"] = {
        "format": LOG_FORMAT,
        "datefmt": LOG_DATEFMT,
    }
    config["handlers"]["file"] = {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "formatter": "file",
        "filename": str(path),
        "when": "midnight",
        "backupCount": LOG_KEEP_DAYS,
        "encoding": "utf-8",
    }
    config["handlers"]["console"] = {
        "class": "logging.StreamHandler",
        "formatter": "console",
        "stream": "ext://sys.stderr",
    }
    config["loggers"]["uvicorn"]["handlers"] = ["default", "file"]
    config["root"] = {"handlers": ["console", "file"], "level": "INFO"}
    return config
