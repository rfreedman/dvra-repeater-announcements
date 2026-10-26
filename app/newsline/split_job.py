"""Run a story split. Whisper is imported only when this runs."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("app.newsline")


class SplitError(RuntimeError):
    pass


def split_report(
    *,
    audio: Path,
    transcript: Path,
    output_dir: Path,
    cache_dir: Path,
    repeater_timeout: int,
) -> None:
    from app.newsline.engine.pipeline import run_forced_align_pipeline
    from app.newsline.engine.progress import PhaseTimer, log as progress_log
    from app.newsline.engine.progress import set_log_file
    from app.newsline.engine.run_summary import RunSummary, write_run_summary
    from app.newsline.engine.split import probe_duration
    from app.newsline.engine.transcript import (
        extract_report_id_from_audio,
        extract_report_id_from_transcript,
        load_transcript,
    )

    audio_id = extract_report_id_from_audio(audio)
    text_id = extract_report_id_from_transcript(transcript)
    if audio_id is None and text_id is None:
        raise SplitError(f"Could not infer a report number from {audio.name} and {transcript.name}")
    if audio_id is not None and text_id is not None and audio_id != text_id:
        raise SplitError(f"Report number mismatch: audio={audio_id}, transcript={text_id}")
    report_id = audio_id if audio_id is not None else text_id
    assert report_id is not None

    output_dir.mkdir(parents=True, exist_ok=True)
    set_log_file(output_dir / "run.log")
    segments = load_transcript(transcript)
    duration = probe_duration(audio)
    timer = PhaseTimer(f"Report {report_id}: {audio.name} ({duration:.1f}s)")
    final, _words, critical, qa_passes, qa_max = run_forced_align_pipeline(
        audio=audio,
        report_id=report_id,
        all_segments=segments,
        duration=duration,
        output_dir=output_dir,
        align_model="base.en",
        cache_dir=cache_dir,
        dry_run=False,
        reencode=True,
        strict=False,
        qa_model="small.en",
        repeater_timeout=repeater_timeout,
    )
    elapsed = timer.done(f"{len(final)} segments")
    summary = RunSummary(
        report_id=report_id,
        status="FAILED" if critical else "SUCCESS",
        wall_clock_sec=elapsed,
        qa_passes=qa_passes,
        qa_max_passes=qa_max,
        segment_count=len(final),
        output_dir=output_dir,
        errors=list(critical),
    )
    summary_path = write_run_summary(summary)
    progress_log(f"Run summary: {summary_path}")
    if critical:
        detail = "; ".join(f"{item.code}: {item.detail}" for item in critical[:5])
        raise SplitError(detail or "Split reported critical issues")
    log.info("Split report %s into %s", report_id, output_dir)
