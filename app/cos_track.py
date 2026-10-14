"""Background COS polling so idle time accrues before Trigger now / scheduled fires."""
from __future__ import annotations

import logging
import threading

from app.config import DRA_CLEAR_POLL_SECONDS, dra_cos_track_enabled, radio_backend
from app.radio import get_radio

log = logging.getLogger("app.cos_track")

_stop = threading.Event()
_thread: threading.Thread | None = None


def _poll_loop() -> None:
    poll = max(0.05, DRA_CLEAR_POLL_SECONDS)
    while not _stop.is_set():
        try:
            radio = get_radio()
            from app.scheduler import nudge_manual_defer_jobs_if_clear_ready

            from app.dra_alsa_mixer import apply_cm119_replug_recovery

            apply_cm119_replug_recovery(radio)
            if not getattr(radio, "ptt_keyed", False):
                warm = getattr(radio, "warm_open_hid", None)
                if warm is not None:
                    warm()
                refresh = getattr(radio, "refresh_cos", None)
                if refresh is not None:
                    refresh()
                nudge_manual_defer_jobs_if_clear_ready()
            from app.home_live import notify_home_live_status_throttled

            notify_home_live_status_throttled()
        except Exception:
            log.warning("COS background poll failed", exc_info=True)
        if _stop.wait(poll):
            break


def start_cos_tracking() -> None:
    global _thread
    if _thread is not None:
        return
    if not dra_cos_track_enabled():
        log.info("DRA COS background tracking disabled (TTS_DRA_COS_TRACK=false)")
        return
    if radio_backend() not in ("dra", "cm119"):
        return
    _stop.clear()
    _thread = threading.Thread(target=_poll_loop, name="dra-cos-track", daemon=True)
    _thread.start()
    log.info("DRA COS background tracking started (poll=%ss)", DRA_CLEAR_POLL_SECONDS)


def stop_cos_tracking() -> None:
    global _thread
    if _thread is None:
        return
    _stop.set()
    _thread.join(timeout=2.0)
    _thread = None
    _stop.clear()
