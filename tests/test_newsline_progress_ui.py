"""Home-panel Newsline step and elapsed time."""

from __future__ import annotations

from app.newsline.engine.progress import (
    PhaseTimer,
    begin_ui_progress,
    end_ui_progress,
    progress,
    ui_snapshot,
)


def test_ui_progress_tracks_step_and_elapsed():
    end_ui_progress()
    begin_ui_progress("Checking the Newsline feed")
    snap = ui_snapshot()
    assert snap["step"] == "Checking the Newsline feed"
    assert snap["elapsed_seconds"] is not None
    assert snap["elapsed_seconds"] >= 0
    progress(2, 9, "ffmpeg 2557_02_story.mp3")
    snap = ui_snapshot()
    assert snap["step"] == "[2/9] ffmpeg 2557_02_story.mp3"
    end_ui_progress()
    snap = ui_snapshot()
    assert snap["step"] is None
    assert snap["elapsed_seconds"] is None


def test_phase_timer_sets_the_ui_step():
    end_ui_progress()
    begin_ui_progress("Processing")
    timer = PhaseTimer("Forced-aligning script to Report2557.mp3")
    try:
        assert ui_snapshot()["step"] == "Forced-aligning script to Report2557.mp3"
    finally:
        timer.done()
        end_ui_progress()
