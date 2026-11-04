"""Hourly Newsline check inside the Thursday–Friday window, plus a manual run."""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.triggers.date import DateTrigger

from app.config import NEWSLINE_DIR
from app.newsline.decode import ffmpeg_available
from app.newsline.download import download_file, fetch_feed, latest_report
from app.newsline.engine.progress import begin_ui_progress, end_ui_progress, set_ui_step, ui_snapshot
from app.newsline.release import parse_release_friday
from app.newsline.split_job import SplitError, split_report
from app.newsline.state import NewsState, load_state, save_state
from app.newsline.window import next_anchor_after, success_in_window, window_for
from app.slots import as_local, zone_for
from app.store import get_store

log = logging.getLogger("app.newsline")

JOB_ID = "newsline-poll"
_guard = threading.Lock()
_in_progress = False


def check_in_progress() -> bool:
    with _guard:
        return _in_progress


def log_ffmpeg_status() -> None:
    if ffmpeg_available():
        return
    log.warning("ffmpeg or ffprobe is not on PATH; Newsline download and playback cannot run")


def _begin() -> bool:
    global _in_progress
    with _guard:
        if _in_progress:
            return False
        _in_progress = True
        return True


def _end() -> None:
    global _in_progress
    with _guard:
        _in_progress = False


def _store(state: NewsState, root=None) -> None:
    save_state(state, root)
    try:
        from app.home_live import notify_home_data_changed

        notify_home_data_changed()
    except Exception:
        log.debug("Newsline status notify failed", exc_info=True)


def _mark_current(state: NewsState) -> None:
    state.error = None
    state.last_outcome = "current"


def _news_root(root=None) -> Path:
    return root if root is not None else NEWSLINE_DIR


def _clear_report_cache(root, number: int) -> None:
    """Remove splitter cache for one report. Playback does not use these files."""
    cache = _news_root(root) / "cache"
    prefixes = (f"Report{number}_", f"{number:04d}_")
    removed = 0
    for folder in (cache / "forced_align", cache / "slices"):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.is_file() and path.name.startswith(prefixes):
                path.unlink(missing_ok=True)
                removed += 1
    if removed:
        log.info("Cleared Newsline split cache for report %s", number)


def _remove_report_files(folder: Path, number: int) -> None:
    for name in (f"Report{number}.mp3", f"nsln{number}.txt"):
        path = folder / name
        if path.is_file():
            path.unlink()
    stories = folder / "stories"
    if stories.is_dir():
        shutil.rmtree(stories)


def _settings_now() -> tuple[datetime, str, str, int, ZoneInfo]:
    document = get_store().document()
    zone = zone_for(document.settings.timezone)
    now = as_local(datetime.now(zone), zone)
    return (
        now,
        document.settings.news_check_start,
        document.settings.news_check_stop,
        document.settings.repeater_timeout_seconds,
        zone,
    )


def run_check(*, manual: bool, root=None) -> NewsState:
    """Fetch, download, and split when a newer report is available."""
    now, start_hhmm, stop_hhmm, timeout, _zone = _settings_now()
    state = load_state(root)
    state.phase = "checking"
    state.working_report = None
    state.last_check_at = now
    _store(state, root)
    log.info("Newsline check started (manual=%s)", manual)
    begin_ui_progress("Checking the Newsline feed")
    downloaded = False
    number: int | None = None
    try:
        if not ffmpeg_available():
            raise OSError("ffmpeg and ffprobe are required")
        report = latest_report(fetch_feed())
        if report is None:
            state.error = "The Newsline feed has no report"
            log.info("Newsline feed has no report")
            return state
        number = report.report_number
        window = window_for(now, start_hhmm, stop_hhmm)
        already = state.report_number == number and state.split_ok
        if already and window is not None and not manual:
            if success_in_window(
                state.completed_at,
                split_ok=True,
                start=window[0],
                stop=window[1],
            ):
                log.info("Newsline report %s was already split this window", number)
                _mark_current(state)
                return state
            if state.completed_at is not None and state.completed_at < window[0]:
                log.info(
                    "Newsline feed is still report %s from before this window; will check again",
                    number,
                )
                _mark_current(state)
                return state
        if already and manual:
            log.info("Newsline report %s is already split", number)
            _mark_current(state)
            return state
        if not manual and state.split_failed_report == number:
            log.info(
                "Newsline report %s already failed to split; not retrying until a newer report or a manual check",
                number,
            )
            return state
        if state.split_failed_report is not None and state.split_failed_report != number:
            _clear_report_cache(root, state.split_failed_report)
            state.split_failed_report = None
        folder = _news_root(root) / str(number)
        folder.mkdir(parents=True, exist_ok=True)
        audio = folder / f"Report{number}.mp3"
        transcript = folder / f"nsln{number}.txt"
        if manual and (
            state.split_failed_report == number
            or audio.is_file()
            or transcript.is_file()
            or (folder / "stories").exists()
        ):
            log.info("Manual Newsline check will re-download report %s", number)
            _remove_report_files(folder, number)
            _clear_report_cache(root, number)
            if state.split_failed_report == number:
                state.split_failed_report = None
        state.working_report = number
        state.phase = "downloading"
        _store(state, root)
        if not audio.is_file():
            set_ui_step(f"Downloading report {number} audio")
            log.info("Downloading Newsline report %s audio", number)
            download_file(report.audio_url, audio)
        else:
            log.info("Newsline audio already on disk: %s", audio)
        if not transcript.is_file():
            set_ui_step(f"Downloading report {number} script")
            log.info("Downloading Newsline report %s script", number)
            download_file(report.script_url, transcript)
        else:
            log.info("Newsline script already on disk: %s", transcript)
        downloaded = True
        state.phase = "splitting"
        set_ui_step("Processing")
        _store(state, root)
        try:
            split_report(
                audio=audio,
                transcript=transcript,
                output_dir=folder / "stories",
                cache_dir=_news_root(root) / "cache",
                repeater_timeout=timeout,
            )
        finally:
            _clear_report_cache(root, number)
        friday = (
            parse_release_friday(transcript.read_text(encoding="utf-8", errors="replace"))
            if transcript.is_file()
            else None
        )
        if friday is None:
            log.warning(
                "Newsline report %s script has no intended Friday; it will not air",
                number,
            )
        state.report_number = number
        state.release_friday = friday
        state.split_ok = True
        state.split_failed_report = None
        state.completed_at = _settings_now()[0]
        state.error = None
        state.last_outcome = "new"
        log.info("Newsline report %s is ready", number)
    except (OSError, SplitError, ValueError) as exc:
        if downloaded and number is not None:
            state.split_failed_report = number
            _clear_report_cache(root, number)
        state.error = str(exc)
        log.warning("Newsline check failed: %s", exc)
    except Exception as exc:
        if downloaded and number is not None:
            state.split_failed_report = number
            _clear_report_cache(root, number)
        state.error = str(exc)
        log.exception("Newsline check failed")
    finally:
        end_ui_progress()
        state.phase = "idle"
        state.working_report = None
        state.last_check_at = _settings_now()[0]
        _store(state, root)
    return state


def _run_and_rearm(*, manual: bool) -> None:
    try:
        run_check(manual=manual)
    finally:
        _end()
        if not manual:
            arm_next_poll(immediate=False)
        else:
            arm_next_poll(immediate=False)


def start_manual_check() -> bool:
    """Start one check on a background thread. False when one is already running."""
    if not _begin():
        log.info("Newsline check requested while one is already running")
        return False
    log.info("Manual Newsline check requested")
    state = load_state()
    state.phase = "checking"
    state.working_report = None
    _store(state)
    threading.Thread(target=_run_and_rearm, kwargs={"manual": True}, name="newsline-check", daemon=True).start()
    return True


def _scheduled_job() -> None:
    if not _begin():
        return
    _run_and_rearm(manual=False)


def arm_next_poll(*, immediate: bool) -> None:
    """Schedule the next anchor, or run now when the process woke inside the window."""
    from app.scheduler import get_scheduler

    scheduler = get_scheduler()
    now, start_hhmm, stop_hhmm, _timeout, zone = _settings_now()
    window = window_for(now, start_hhmm, stop_hhmm)
    if scheduler is not None:
        existing = scheduler.get_job(JOB_ID)
        if existing is not None and (window is None or not immediate):
            scheduler.remove_job(JOB_ID)
    if window is None:
        return
    state = load_state()
    if success_in_window(state.completed_at, split_ok=state.split_ok, start=window[0], stop=window[1]):
        log.info("Newsline window already has a split report; no further checks")
        return
    if immediate and not check_in_progress():
        if _begin():
            threading.Thread(
                target=_run_and_rearm,
                kwargs={"manual": False},
                name="newsline-check",
                daemon=True,
            ).start()
        return
    nxt = next_anchor_after(now, window[0], window[1])
    if nxt is None or scheduler is None:
        return
    scheduler.add_job(
        _scheduled_job,
        DateTrigger(run_date=as_local(nxt, zone)),
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=int(timedelta(minutes=5).total_seconds()),
    )
    log.info("Next Newsline check at %s", nxt.isoformat())


def install_news_poll() -> None:
    arm_next_poll(immediate=True)


def status_payload() -> dict[str, object]:
    state = load_state()
    stories = state.stories_dir()
    ready = False
    if stories is not None:
        from app.newsline.playlist import stories_ready

        ready = stories_ready(stories)
    from app.newsline.session import bulletin_session

    progress = ui_snapshot()
    return {
        "report_number": state.report_number,
        "split_ok": state.split_ok,
        "ready": ready,
        "phase": state.phase,
        "error": state.error,
        "completed_at": state.completed_at.isoformat() if state.completed_at else None,
        "last_check_at": state.last_check_at.isoformat() if state.last_check_at else None,
        "in_progress": check_in_progress(),
        "playing": bulletin_session.active,
        "working_report": state.working_report,
        "outcome": state.last_outcome,
        "step": progress["step"],
        "elapsed_seconds": progress["elapsed_seconds"],
    }
