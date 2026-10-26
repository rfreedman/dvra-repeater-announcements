from __future__ import annotations

import sys
from pathlib import Path

from app.newsline.engine.export_qa import (
    ACTIONABLE_SLICE_CODES,
    MAX_QA_PASSES,
    run_export_qa_loop,
)
from app.newsline.engine.forced_align import align_segments_forced
from app.newsline.engine.models import Boundary, Segment, WordTiming
from app.newsline.engine.split import (
    export_boundaries_json,
    slice_audio,
    write_alignment_report,
)
from app.newsline.engine.verify import ExportBoundaryIssue
from app.newsline.engine.verify_export import verify_post_export

CRITICAL_ISSUE_CODES = frozenset(
    {
        "overlap",
        "duration_mismatch",
        "missing_file",
        "forced_align_gap",
        "forced_align_missing",
        "non_monotonic",
    }
) | ACTIONABLE_SLICE_CODES


def is_critical(issue: ExportBoundaryIssue, *, strict: bool = False) -> bool:
    if issue.code == "clip_end":
        return strict
    # Bleed / bad handoffs are always critical — never ship silently.
    if issue.code in ACTIONABLE_SLICE_CODES:
        return True
    return issue.code in CRITICAL_ISSUE_CODES


def _dedupe_issues(issues: list[ExportBoundaryIssue]) -> list[ExportBoundaryIssue]:
    seen: set[tuple[int, str, str]] = set()
    out: list[ExportBoundaryIssue] = []
    for it in issues:
        key = (it.segment_index, it.code, it.detail[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _print_issues(
    export: list[ExportBoundaryIssue],
    *,
    pass_num: int = 1,
    max_passes: int = 1,
) -> None:
    if not export:
        return
    print(f"Pass {pass_num}/{max_passes} validation:", file=sys.stderr)
    for it in export:
        mark = "!!" if is_critical(it) else "  !"
        print(f"  {mark} {it.title}: [{it.code}] {it.detail}", file=sys.stderr)


def _qa_forced_boundaries(
    boundaries: tuple[Boundary, ...],
) -> list[ExportBoundaryIssue]:
    issues: list[ExportBoundaryIssue] = []
    for i, b in enumerate(boundaries):
        if b.match_score < 50.0:
            issues.append(
                ExportBoundaryIssue(
                    b.segment_index,
                    b.title,
                    "forced_align_missing",
                    b.anchor_snippet or "no align span",
                )
            )
        if i + 1 < len(boundaries):
            nxt = boundaries[i + 1]
            if nxt.start + 0.02 < b.start:
                issues.append(
                    ExportBoundaryIssue(
                        b.segment_index,
                        b.title,
                        "non_monotonic",
                        f"start {b.start:.2f}s after next {nxt.start:.2f}s",
                    )
                )
            if b.end > nxt.start + 0.05:
                issues.append(
                    ExportBoundaryIssue(
                        b.segment_index,
                        b.title,
                        "overlap",
                        f"end {b.end:.2f}s overlaps next start {nxt.start:.2f}s",
                    )
                )
    return issues


def run_forced_align_pipeline(
    *,
    audio: Path,
    report_id: int,
    all_segments: tuple[Segment, ...],
    duration: float,
    output_dir: Path,
    align_model: str,
    cache_dir: Path | None,
    dry_run: bool,
    reencode: bool,
    strict: bool,
    qa_model: str = "small.en",
    qa_workers: int | None = None,
    repeater_timeout: int = 180,
) -> tuple[tuple[Boundary, ...], list[WordTiming], list[ExportBoundaryIssue], int, int]:
    """
    Forced-align → quiet cuts → selective handoff inspect → export → QA loop.

    Story→story cuts stay on script spans + RMS quiet, with a narrow residual
    check when unscripted speech precedes the next opener. Inspect/QA landmark
    rewrites apply to intro/break/close only. Slice ASR still checks exports
    (parallel Whisper workers by default).

    ``repeater_timeout`` is the repeater timeout in whole seconds; export packing
    uses timeout−2s.

    Returns ``(boundaries, words, critical_issues, qa_passes, qa_max_passes)``.
    """
    from app.newsline.engine.progress import PhaseTimer, log

    align = PhaseTimer(f"Forced-aligning script to {audio.name} (model={align_model})")
    boundaries, words, energy_frames = align_segments_forced(
        all_segments,
        audio,
        duration,
        model_name=align_model,
        cache_dir=cache_dir,
    )
    align.done(f"{len(words)} words, {len(boundaries)} export segments")

    issues = _qa_forced_boundaries(boundaries)
    post_issues: list[ExportBoundaryIssue] = []
    qa_remaining: list[ExportBoundaryIssue] = []
    qa_passes = 0
    qa_max_passes = MAX_QA_PASSES
    mutable = list(boundaries)

    if not dry_run:
        export = PhaseTimer(f"Exporting MP3s (reencode={reencode})")
        slice_audio(
            audio,
            tuple(mutable),
            output_dir,
            report_id,
            segments=all_segments,
            words=None,
            seg_by_index={s.index: s for s in all_segments},
            reencode=reencode,
            repeater_timeout=repeater_timeout,
        )
        export.done(f"{len(mutable)} files → {output_dir.name}/")
        post_issues = verify_post_export(
            output_dir,
            report_id,
            tuple(mutable),
            all_segments,
            words=[],
            repeater_timeout=repeater_timeout,
        )
        log("Starting export QA loop (slice ASR + energy + master handoffs) …")
        mutable, qa_remaining, qa_passes = run_export_qa_loop(
            audio=audio,
            report_id=report_id,
            boundaries=mutable,
            segments=all_segments,
            words=words,
            output_dir=output_dir,
            audio_duration=duration,
            cache_dir=cache_dir,
            reencode=reencode,
            qa_model=qa_model,
            energy_frames=energy_frames,
            qa_workers=qa_workers,
            repeater_timeout=repeater_timeout,
        )
        # Duration check after final export
        post_issues = verify_post_export(
            output_dir,
            report_id,
            tuple(mutable),
            all_segments,
            words=[],
            repeater_timeout=repeater_timeout,
        )

    boundaries = tuple(mutable)
    combined = _dedupe_issues(issues + post_issues + qa_remaining)
    _print_issues(combined)
    critical = [it for it in combined if is_critical(it, strict=strict)]

    export_boundaries_json(output_dir / "boundaries.json", boundaries)
    write_alignment_report(output_dir / "alignment_report.txt", boundaries)
    return boundaries, words, critical, qa_passes, qa_max_passes
