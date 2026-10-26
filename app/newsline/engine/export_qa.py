"""
Post-export QA: ASR each story file, energy-check cuts, auto-correct, re-export.

Offline weekly pipeline — accuracy over speed. Bleed / clipped callsigns must
never ship silently.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from app.newsline.engine.correct import apply_slice_boundary_corrections
from app.newsline.engine.handoff_inspect import (
    _best_opener,
    _best_signoff,
    _cut_between_landmarks,
    transcribe_handoff_window,
)
from app.newsline.engine.models import Boundary, Segment, SegmentKind, WordTiming
from app.newsline.engine.silence_cuts import (
    load_mono_pcm16,
    rms_frames,
)
from app.newsline.engine.split import slice_audio
from app.newsline.engine.verify import ExportBoundaryIssue
from app.newsline.engine.verify_slices import (
    _slice_cache_path,
    verify_export_slices,
)

# Codes that mean a handoff cut is wrong and must be fixed or fail the run.
# Issues that prove a cut is wrong and have a measured fix.
ACTIONABLE_SLICE_CODES = frozenset(
    {
        "slice_prev_signoff_in_head",
        "slice_next_in_tail",
        "slice_break_in_head",
        "slice_opener_lead_clipped",
        "slice_missing_opener",
        "handoff_landmarks_disagree",
    }
)
# Soft warnings: logged but do not fail the run or force blind re-exports.
# cut_in_speech without a measured quieter global= time stays non-blocking;
# when detail includes global= it is promoted to actionable below.
SOFT_SLICE_CODES = frozenset(
    {
        "cut_in_speech",
    }
)

MAX_QA_PASSES = 3
# Prefer a stronger English model for offline QA (time is not constrained).
_DEFAULT_QA_MODEL = "small.en"


def _rms_near(frames: list[tuple[float, float]], t: float) -> float:
    if not frames:
        return 0.0
    return min(frames, key=lambda fr: abs(fr[0] - t))[1]


def verify_cuts_energy(
    boundaries: tuple[Boundary, ...] | list[Boundary],
    segments: tuple[Segment, ...],
    energy_frames: list[tuple[float, float]],
) -> list[ExportBoundaryIssue]:
    """
    Soft-flag cuts that sit in sustained loud energy with quieter alternatives nearby.

    Abutting callsign→opener handoffs often have no true silence; those are OK
    when landmarks agree. Only flag when a clearly quieter spot exists within
    ±0.35s (cut landed mid-callsign / mid-word).
    """
    seg_by = {s.index: s for s in segments}
    issues: list[ExportBoundaryIssue] = []
    for i in range(len(boundaries) - 1):
        a, b = boundaries[i], boundaries[i + 1]
        if abs(a.end - b.start) > 0.15:
            continue  # dual gaps checked by beep/billboard logic
        cut = 0.5 * (a.end + b.start)
        sa = seg_by.get(a.segment_index)
        if sa and sa.kind == SegmentKind.BILLBOARD:
            continue
        at = _rms_near(energy_frames, cut)
        if at < 1500:
            continue
        band = [(tt, r) for tt, r in energy_frames if abs(tt - cut) <= 0.35]
        if not band:
            continue
        t_min, r_min = min(band, key=lambda tr: tr[1])
        # Only complain if a much quieter alternative exists nearby.
        if r_min < 600 and at > r_min * 3.0 and abs(t_min - cut) >= 0.08:
            issues.append(
                ExportBoundaryIssue(
                    a.segment_index,
                    a.title,
                    "cut_in_speech",
                    f"shared cut @{cut:.2f}s RMS={at:.0f}; quieter "
                    f"@{t_min:.2f}s RMS={r_min:.0f} global={t_min:.2f}s",
                )
            )
    return issues


def verify_handoffs_on_master(
    audio: Path,
    boundaries: tuple[Boundary, ...] | list[Boundary],
    segments: tuple[Segment, ...],
    energy_frames: list[tuple[float, float]],
    audio_duration: float,
    *,
    asr_model: str,
) -> tuple[list[ExportBoundaryIssue], list[tuple[int, int, float]]]:
    """
    Re-ASR story→close handoffs on the master (not story→story).

    Story→story cuts stay on forced-align + quiet (plus residual-before-opener
    in inspection); free-ASR sign-off rewrites were clipping callsigns.
    Story→close still benefits from landmark checks near the closing cart.
    """
    seg_by = {s.index: s for s in segments}
    issues: list[ExportBoundaryIssue] = []
    fixes: list[tuple[int, int, float]] = []

    from app.newsline.engine.progress import progress

    handoff_idxs = [
        i
        for i in range(len(boundaries) - 1)
        if (
            (sa := seg_by.get(boundaries[i].segment_index)) is not None
            and (sb := seg_by.get(boundaries[i + 1].segment_index)) is not None
            and sa.kind == SegmentKind.STORY
            and sb.kind == SegmentKind.CLOSE
            and abs(boundaries[i].end - boundaries[i + 1].start) <= 0.2
        )
    ]
    for n, i in enumerate(handoff_idxs, start=1):
        a, b = boundaries[i], boundaries[i + 1]
        sa, sb = seg_by[a.segment_index], seg_by[b.segment_index]
        progress(
            n,
            max(1, len(handoff_idxs)),
            f"master handoff {a.segment_index}→{b.segment_index} ({a.title[:36]})",
        )

        cut = 0.5 * (a.end + b.start)
        t0 = max(0.0, cut - 4.0)
        t1 = min(audio_duration, cut + 4.0)
        words = transcribe_handoff_window(audio, t0, t1, model_name=asr_model)
        signoff = _best_signoff(words, sa, not_after=cut + 2.5)
        opener = _best_opener(
            words,
            sb,
            not_before=(signoff[1] - 0.2) if signoff else (cut - 2.5),
        )
        if signoff is None or opener is None:
            continue
        # Extend sign-off through residual callsign energy.
        from app.newsline.engine.handoff_inspect import _extend_through_speech

        signoff_end = _extend_through_speech(energy_frames, signoff[1])
        opener_start = opener[0]
        if opener_start < signoff_end - 0.15:
            continue
        ideal = _cut_between_landmarks(energy_frames, signoff_end, opener_start)
        if abs(ideal - cut) < 0.12:
            continue
        # Meaningful disagreement: provisional cut is early (clips callsign) or late.
        issues.append(
            ExportBoundaryIssue(
                a.segment_index,
                a.title,
                "handoff_landmarks_disagree",
                f"provisional cut @{cut:.2f}s vs measured "
                f"signoff@{signoff_end:.2f} opener@{opener_start:.2f} "
                f"ideal@{ideal:.2f}s global={ideal:.2f}s",
            )
        )
        fixes.append((i, i + 1, ideal))
    return issues, fixes


def _changed_segment_indices(
    before: list[Boundary] | tuple[Boundary, ...],
    after: list[Boundary] | tuple[Boundary, ...],
    *,
    tol: float = 0.02,
) -> set[int]:
    """Segment indices whose start/end moved (plus immediate neighbors)."""
    by_before = {b.segment_index: b for b in before}
    changed: set[int] = set()
    ordered = [b.segment_index for b in after]
    for b in after:
        prev = by_before.get(b.segment_index)
        if prev is None:
            changed.add(b.segment_index)
            continue
        if abs(prev.start - b.start) > tol or abs(prev.end - b.end) > tol:
            changed.add(b.segment_index)
    # Neighbors share a cut — their export files also change at the edge.
    if changed and ordered:
        idx_of = {si: i for i, si in enumerate(ordered)}
        extra: set[int] = set()
        for si in changed:
            i = idx_of.get(si)
            if i is None:
                continue
            if i > 0:
                extra.add(ordered[i - 1])
            if i + 1 < len(ordered):
                extra.add(ordered[i + 1])
        changed |= extra
    return changed


def _invalidate_slice_caches(
    output_dir: Path,
    report_id: int,
    boundaries: tuple[Boundary, ...] | list[Boundary],
    segments: tuple[Segment, ...],
    cache_dir: Path | None,
    qa_model: str,
    *,
    only_segment_indices: set[int] | None = None,
    repeater_timeout: int = 180,
) -> None:
    if cache_dir is None:
        return
    from app.newsline.engine.split import plan_export_slots

    for slot in plan_export_slots(
        output_dir,
        report_id,
        tuple(boundaries),
        segments,
        repeater_timeout=repeater_timeout,
    ):
        if slot.is_station_id:
            continue
        if only_segment_indices is not None and not any(
            b.segment_index in only_segment_indices for b in slot.boundaries
        ):
            continue
        base = _slice_cache_path(cache_dir, slot.path, qa_model)
        for cache in (
            base,
            base.with_name(base.stem + "_qa.json"),
            base.with_name(base.stem + "_full.json"),
        ):
            if cache.is_file():
                with contextlib.suppress(OSError):
                    cache.unlink()


def _apply_master_fixes(
    boundaries: list[Boundary],
    fixes: list[tuple[int, int, float]],
) -> float:
    max_delta = 0.0
    for i_a, i_b, cut in fixes:
        if i_a >= len(boundaries) or i_b >= len(boundaries):
            continue
        a, b = boundaries[i_a], boundaries[i_b]
        cut = max(a.start + 0.4, min(cut, b.end - 0.4 if b.end > cut else cut))
        max_delta = max(max_delta, abs(a.end - cut), abs(b.start - cut))
        boundaries[i_a] = Boundary(
            segment_index=a.segment_index,
            title=a.title,
            start=a.start,
            end=cut,
            match_score=a.match_score,
            anchor_snippet=f"qa master-fix →{cut:.2f}",
        )
        boundaries[i_b] = Boundary(
            segment_index=b.segment_index,
            title=b.title,
            start=cut,
            end=b.end,
            match_score=b.match_score,
            anchor_snippet=b.anchor_snippet,
        )
    return max_delta


def run_export_qa_loop(
    *,
    audio: Path,
    report_id: int,
    boundaries: list[Boundary],
    segments: tuple[Segment, ...],
    words: list[WordTiming],
    output_dir: Path,
    audio_duration: float,
    cache_dir: Path | None,
    reencode: bool,
    qa_model: str = _DEFAULT_QA_MODEL,
    max_passes: int = MAX_QA_PASSES,
    energy_frames: list[tuple[float, float]] | None = None,
    qa_workers: int | None = None,
    repeater_timeout: int = 180,
) -> tuple[list[Boundary], list[ExportBoundaryIssue], int]:
    """
    Export-aware QA: slice ASR + energy + master handoff landmarks, correct, re-export.

    Returns ``(boundaries, remaining_actionable_issues, passes_completed)``.
    """
    from app.newsline.engine.progress import PhaseTimer, log

    if energy_frames is None:
        energy = PhaseTimer("  measuring energy for cut QA")
        samples, rate = load_mono_pcm16(audio)
        energy_frames = rms_frames(samples, rate, frame_ms=10.0)
        energy.done(f"{len(energy_frames)} frames")
    else:
        log(f"  reusing {len(energy_frames)} energy frames for cut QA")

    remaining: list[ExportBoundaryIssue] = []
    passes_completed = 0
    for pass_num in range(1, max_passes + 1):
        passes_completed = pass_num
        qa_pass = PhaseTimer(
            f"  export QA pass {pass_num}/{max_passes} (model={qa_model})"
        )
        log("    slice ASR checks …")
        slice_issues = verify_export_slices(
            output_dir,
            report_id,
            tuple(boundaries),
            segments,
            model_name=qa_model,
            cache_dir=cache_dir,
            full_words=words,
            qa_workers=qa_workers,
            repeater_timeout=repeater_timeout,
        )
        log("    energy-at-cut checks …")
        energy_issues = verify_cuts_energy(boundaries, segments, energy_frames)
        log("    master handoff re-ASR …")
        master_issues, master_fixes = verify_handoffs_on_master(
            audio,
            boundaries,
            segments,
            energy_frames,
            audio_duration,
            asr_model=qa_model,
        )
        soft = [
            it
            for it in (slice_issues + energy_issues)
            if it.code in SOFT_SLICE_CODES
        ]
        all_issues = [
            it
            for it in (slice_issues + energy_issues + master_issues)
            if it.code in ACTIONABLE_SLICE_CODES
        ]
        # cut_in_speech with a measured quieter global= time is actionable.
        for it in energy_issues:
            if it.code == "cut_in_speech" and "global=" in it.detail:
                all_issues.append(it)
        # Dedup by (segment, code)
        seen: set[tuple[int, str]] = set()
        deduped: list[ExportBoundaryIssue] = []
        for it in all_issues:
            key = (it.segment_index, it.code)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(it)
        all_issues = deduped

        if soft:
            log(f"  {len(soft)} soft QA note(s) (non-blocking):")
            for it in soft[:8]:
                print(f"      · [{it.code}] {it.title}: {it.detail[:90]}", flush=True)

        if not all_issues and not master_fixes:
            qa_pass.done("clean")
            return boundaries, [], passes_completed

        log(f"  {len(all_issues)} actionable issue(s) on pass {pass_num}:")
        for it in all_issues:
            print(f"    ! [{it.code}] {it.title}: {it.detail[:100]}", flush=True)

        before = list(boundaries)
        delta = 0.0
        if master_fixes:
            delta = max(delta, _apply_master_fixes(boundaries, master_fixes))
        if all_issues:
            delta = max(
                delta,
                apply_slice_boundary_corrections(
                    boundaries,
                    all_issues,
                    segments,
                    words,
                    min_score=70.0,
                    pass_num=pass_num,
                ),
            )
        # Sync shared cuts only (never collapse dual billboard/break gaps).
        for i in range(len(boundaries) - 1):
            a, b = boundaries[i], boundaries[i + 1]
            if abs(a.end - b.start) <= 0.12:
                mid = 0.5 * (a.end + b.start)
                boundaries[i] = Boundary(
                    segment_index=a.segment_index,
                    title=a.title,
                    start=a.start,
                    end=mid,
                    match_score=a.match_score,
                    anchor_snippet=a.anchor_snippet,
                )
                boundaries[i + 1] = Boundary(
                    segment_index=b.segment_index,
                    title=b.title,
                    start=mid,
                    end=b.end,
                    match_score=b.match_score,
                    anchor_snippet=b.anchor_snippet,
                )

        if delta < 0.04 and not master_fixes:
            qa_pass.done(f"negligible movement (Δ={delta:.2f}s); stopping")
            remaining = all_issues
            break

        touched = _changed_segment_indices(before, boundaries)
        qa_pass.done(f"{len(all_issues)} issues, max Δ={delta:.2f}s")
        reexp = PhaseTimer(f"  re-exporting after QA corrections (max Δ={delta:.2f}s)")
        _invalidate_slice_caches(
            output_dir,
            report_id,
            boundaries,
            segments,
            cache_dir,
            qa_model,
            only_segment_indices=touched or None,
            repeater_timeout=repeater_timeout,
        )
        slice_audio(
            audio,
            tuple(boundaries),
            output_dir,
            report_id,
            segments=segments,
            words=None,
            seg_by_index={s.index: s for s in segments},
            reencode=reencode,
            only_segment_indices=touched or None,
            repeater_timeout=repeater_timeout,
        )
        reexp.done()
        remaining = all_issues
    else:
        # Final verify after last pass
        log("  final QA verify after last correction pass …")
        slice_issues = verify_export_slices(
            output_dir,
            report_id,
            tuple(boundaries),
            segments,
            model_name=qa_model,
            cache_dir=cache_dir,
            full_words=words,
            qa_workers=qa_workers,
            repeater_timeout=repeater_timeout,
        )
        energy_issues = verify_cuts_energy(boundaries, segments, energy_frames)
        master_issues, _ = verify_handoffs_on_master(
            audio,
            boundaries,
            segments,
            energy_frames,
            audio_duration,
            asr_model=qa_model,
        )
        remaining = [
            it
            for it in (slice_issues + energy_issues + master_issues)
            if it.code in ACTIONABLE_SLICE_CODES
        ]

    return boundaries, remaining, passes_completed
