from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from app.newsline.engine.pipeline import run_forced_align_pipeline
from app.newsline.engine.run_summary import RunSummary, write_run_summary
from app.newsline.engine.split import probe_duration
from app.newsline.engine.transcript import (
    extract_report_id_from_audio,
    extract_report_id_from_transcript,
    load_transcript,
)


def _resolve_report_id(audio: Path, transcript: Path, force: bool) -> int:
    audio_id = extract_report_id_from_audio(audio)
    txt_id = extract_report_id_from_transcript(transcript)
    if audio_id is None and txt_id is None:
        print(
            "Could not infer report id from filenames; use Report####.mp3 and nsln####.txt",
            file=sys.stderr,
        )
        sys.exit(1)
    if audio_id is not None and txt_id is not None and audio_id != txt_id and not force:
        print(
            f"Report id mismatch: audio={audio_id}, transcript={txt_id} (use --force to ignore)",
            file=sys.stderr,
        )
        sys.exit(1)
    return audio_id if audio_id is not None else txt_id  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newsline-split",
        description=(
            "Split an Amateur Radio Newsline MP3 into one file per story by "
            "forced-aligning the official nsln####.txt transcript to the audio."
        ),
    )
    parser.add_argument("--audio", type=Path, required=True, help="Path to Report####.mp3")
    parser.add_argument("--transcript", type=Path, required=True, help="Path to nsln####.txt")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output MP3s (default: <audio_dir>/<audio_stem>_stories)",
    )
    parser.add_argument(
        "--align-model",
        default="base.en",
        help="stable-ts / Whisper model for forced alignment (default: base.en)",
    )
    parser.add_argument(
        "--qa-model",
        default="small.en",
        help=(
            "Whisper model for post-export slice/handoff QA (default: small.en). "
            "Offline weekly runs can use small.en or medium.en for accuracy."
        ),
    )
    parser.add_argument(
        "--qa-workers",
        type=int,
        default=None,
        help=(
            "Parallel Whisper workers for slice QA (default: min(4, CPU count), "
            "or ARNEWSLINE_QA_WORKERS). Use 1 to force sequential ASR."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/arnewsline"),
        help="Cache directory for forced-align JSON",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat soft QA warnings as critical for exit code",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Alignment and validation only; no MP3 export",
    )
    parser.add_argument(
        "--stream-copy",
        action="store_true",
        help="Use ffmpeg stream copy instead of re-encode (faster, less accurate cuts)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if report ids in filenames disagree",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or write forced-align cache",
    )
    parser.add_argument(
        "--repeater-timeout",
        type=int,
        default=180,
        metavar="SEC",
        help=(
            "Repeater timeout in whole seconds (default: 180). Consecutive "
            "intro/billboard/story/close segments are packed into one MP3 while "
            "under timeout−2s. Never packs across BREAK. Use 0 to disable packing."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    # Agent/CI shells are often non-TTY; keep progress visible immediately.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    try:
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = build_parser()
    args = parser.parse_args(argv)

    audio: Path = args.audio.expanduser().resolve()
    transcript: Path = args.transcript.expanduser().resolve()
    if not audio.is_file():
        print(f"Audio file not found: {audio}", file=sys.stderr)
        sys.exit(1)
    if not transcript.is_file():
        print(f"Transcript not found: {transcript}", file=sys.stderr)
        sys.exit(1)

    report_id = _resolve_report_id(audio, transcript, args.force)
    output_dir: Path = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else audio.parent / f"{audio.stem}_stories"
    )

    all_segments = load_transcript(transcript)
    duration = probe_duration(audio)
    from app.newsline.engine.progress import PhaseTimer, log, set_log_file

    output_dir.mkdir(parents=True, exist_ok=True)
    run_log = output_dir / "run.log"
    set_log_file(run_log)
    log(f"Progress log: {run_log}  (tail -f {run_log})")

    run = PhaseTimer(
        f"Report {report_id}: {audio.name} ({duration:.1f}s audio, "
        f"{len(all_segments)} transcript blocks)"
    )

    cache_dir = None if args.no_cache else args.cache_dir
    reencode = not args.stream_copy

    final, _words, critical, qa_passes, qa_max_passes = run_forced_align_pipeline(
        audio=audio,
        report_id=report_id,
        all_segments=all_segments,
        duration=duration,
        output_dir=output_dir,
        align_model=args.align_model,
        cache_dir=cache_dir,
        dry_run=args.dry_run,
        reencode=reencode,
        strict=args.strict,
        qa_model=args.qa_model,
        qa_workers=args.qa_workers,
        repeater_timeout=args.repeater_timeout,
    )

    log(f"Wrote {output_dir / 'boundaries.json'}")
    for b in final:
        print(
            f"  [{b.segment_index:02d}] {b.start:7.1f}s–{b.end:7.1f}s  "
            f"score={b.match_score:5.1f}  {b.title[:50]}",
            flush=True,
        )

    if args.dry_run:
        log("Dry run: skipping MP3 export.")
    elapsed = run.done(f"{len(final)} segments → {output_dir.name}/")

    summary = RunSummary(
        report_id=report_id,
        status="FAILED" if critical else "SUCCESS",
        wall_clock_sec=elapsed,
        qa_passes=qa_passes,
        qa_max_passes=qa_max_passes,
        segment_count=len(final),
        output_dir=output_dir,
        errors=list(critical),
    )
    summary_path = write_run_summary(summary)
    log(f"Run summary: {summary_path}")

    if critical:
        print(
            f"{len(critical)} critical issue(s) remain; try --align-model small.en "
            f"or inspect alignment_report.txt / run_summary.txt.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
