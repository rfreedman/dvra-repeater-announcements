"""Optional verbose logging for busy / defer / clear / retry (``TTS_FIRE_VERBOSE_LOG``)."""
from __future__ import annotations

import logging

from app.config import fire_verbose_log_enabled

log = logging.getLogger("app.fire.trace")


def fire_trace(msg: str, *args: object) -> None:
    if fire_verbose_log_enabled():
        log.info(msg, *args)
