"""Application startup checks (DRA hardware, etc.)."""
from __future__ import annotations

import os
import sys


class StartupError(Exception):
    """Fatal startup problem; message is safe to show operators."""


def _message_for_os_error(exc: OSError) -> str:
    return (
        "Could not open the DRA USB HID device. "
        "On Linux, check udev rules, replug the DRA, and confirm your user is in the "
        "plugdev group (see docs/NUC-notes.md). "
        f"({exc})"
    )


def init_radio_at_startup() -> None:
    """Eager DRA/stub radio init so misconfiguration fails before serving traffic."""
    from app.config import radio_backend
    from app.radio import create_radio_from_config, set_radio

    backend = radio_backend()
    if backend in ("", "stub"):
        return
    try:
        radio = create_radio_from_config()
        set_radio(radio)
        warm = getattr(radio, "warm_open_hid", None)
        if warm is not None:
            warm()
    except StartupError:
        raise
    except (RuntimeError, ValueError) as exc:
        raise StartupError(str(exc)) from None
    except OSError as exc:
        raise StartupError(_message_for_os_error(exc)) from None
    except Exception as exc:
        raise StartupError(f"DRA radio startup failed: {exc}") from None


def fail_startup(message: str) -> None:
    """Log and terminate the process without raising (avoids lifespan tracebacks)."""
    import logging

    logging.getLogger("app.server").error(message)
    sys.stderr.write(message.rstrip() + "\n")
    sys.stderr.flush()
    os._exit(1)
