"""Rewrite intro, break, and close boundaries from handoff landmarks.

Landmark matching lives in ``handoff_landmarks``, quiet-cut placement in
``handoff_cuts``, and window ASR in ``handoff_window``. Script phrases those
steps share with alignment and slice QA come from ``script_phrases``.
"""

from __future__ import annotations

from pathlib import Path

from app.newsline.engine.handoff_cuts import (
    LONG_GAP_SEC,
    MIN_CART_SEC,
    clamp_before_opener,
    cut_after_signoff,
    cut_before_opener,
    cut_between_landmarks,
    dual_cuts_before_opener,
    quiet_just_before,
    snap_past_sting_callsign,
)
from app.newsline.engine.handoff_landmarks import best_opener, best_signoff
from app.newsline.engine.handoff_window import transcribe_handoff_window
from app.newsline.engine.models import Boundary, Segment, SegmentKind
from app.newsline.engine.silence_cuts import (
    cut_in_silence_gap,
    extend_through_speech,
    find_cut_after_tone_beeps,
    longest_quiet_run,
    quiet_runs,
)


def refine_handoff_from_inspection(
    audio: Path,
    energy_frames: list[tuple[float, float]],
    provisional_cut: float,
    prev_seg: Segment,
    next_seg: Segment,
    *,
    audio_duration: float,
    asr_model: str = "base.en",
    half_window: float = 4.0,
    allow_dual: bool = False,
    keep_end: float | None = None,
    keep_start: float | None = None,
) -> tuple[float, float, str]:
    """
    Empirically locate previous sign-off / intro tail and next opener via ASR,
    then cut in measured silence between those landmarks.

    Returns ``(end_of_prev, start_of_next, detail)``. For long cart gaps (when
    ``allow_dual``) the two times differ; otherwise they match.

    ``keep_end`` / ``keep_start`` preserve an existing dual edge when only one
    landmark is found (avoids collapsing a real cart gap).
    """
    edge_a = keep_end if keep_end is not None else provisional_cut
    edge_b = keep_start if keep_start is not None else provisional_cut
    w = half_window
    # Intro→story: long cart + location sting before the scripted opener.
    # Search far enough *left* to hear the scripted closer ("…right now") before
    # the cart — a ±14s window around a collapsed provisional cut often starts
    # mid-cart and misses the intro sign-off entirely (Report 2550).
    if prev_seg.kind == SegmentKind.INTRO:
        t0 = max(0.0, min(edge_a, provisional_cut) - 22.0)
        # Search far enough right to hear cart, "now reporting…", and story opener.
        t1 = min(audio_duration, max(edge_b, provisional_cut) + 18.0)
    elif prev_seg.kind == SegmentKind.BREAK:
        # Station-ID beeps sit between break speech and the next opener.
        t0 = max(0.0, min(edge_a, provisional_cut) - 4.0)
        t1 = min(audio_duration, max(edge_b, provisional_cut) + 14.0)
    elif next_seg.kind == SegmentKind.BREAK:
        # Story→break: need the full sign-off/callsign before "Time for you…".
        # Narrow ±4s windows often drop the sign-off and cut mid-callsign.
        t0 = max(0.0, provisional_cut - 8.0)
        t1 = min(audio_duration, provisional_cut + 6.0)
    else:
        t0 = max(0.0, provisional_cut - w)
        t1 = min(audio_duration, provisional_cut + w)

    # Break → story: energy finds the post-beep quiet even when ASR glues
    # the opener onto the tone train. Search well *before* provisional/edge —
    # forced-align often parks the cut mid-train already.
    if prev_seg.kind == SegmentKind.BREAK:
        beep_lo = max(0.0, min(edge_a, provisional_cut) - 12.0)
        beep_hi = min(audio_duration, max(edge_b, provisional_cut) + 14.0)
        beep_cut = find_cut_after_tone_beeps(
            energy_frames,
            beep_lo,
            beep_hi,
        )
        if beep_cut is not None:
            # End break VO at the quiet *before* the beep train (not the long
            # post-beep rest, and not edge_a which is often mid-train already).
            cut_a = edge_a
            pre_lo = max(0.0, min(edge_a, beep_cut) - 12.0)
            pre_candidates = quiet_runs(
                energy_frames,
                pre_lo,
                max(pre_lo + 0.5, beep_cut - 0.3),
                min_silence_sec=0.15,
            )
            # Must end well before beep_cut so we don't pick the post-beep gap.
            pre_beep = [
                g
                for g in pre_candidates
                if g[1] <= beep_cut - 0.8 and g[1] - g[0] >= 0.2
            ]
            if pre_beep:
                gap = max(pre_beep, key=lambda g: (g[1] - g[0], g[1]))
                cut_a = gap[0] + min(0.08, 0.25 * (gap[1] - gap[0]))
            beep_cut = max(beep_cut, cut_a + 0.3)
            return (
                cut_a,
                beep_cut,
                f"inspect post-beep →{cut_a:.2f}/{beep_cut:.2f}",
            )

    words = transcribe_handoff_window(audio, t0, t1, model_name=asr_model)

    signoff = best_signoff(words, prev_seg, not_after=provisional_cut + 2.5)
    opener_floor = (signoff[1] - 0.15) if signoff is not None else (provisional_cut - 2.5)
    opener = best_opener(words, next_seg, not_before=opener_floor)

    # Name match often ends before the callsign — walk energy forward into quiet,
    # stopping at the next opener so mid-callsign dips are not treated as the cut.
    # Intro closer sits before a long spoken cart: do not extend into the cart.
    if signoff is not None and prev_seg.kind != SegmentKind.INTRO:
        next_speech = opener[0] if opener is not None else None
        # Whisper sometimes timestamps the next opener mid-callsign (e.g. "time"
        # during W2DIY on 2543_07). That early landmark stops extension and cuts
        # the callsign. Prefer the provisional quiet cut when it is later.
        if (
            next_speech is not None
            and next_speech < provisional_cut - 0.15
        ):
            next_speech = provisional_cut
        extended = extend_through_speech(
            energy_frames,
            signoff[1],
            max_extend=5.0,
            min_quiet_sec=0.25,
            next_speech_start=next_speech,
        )
        signoff = (signoff[0], extended, signoff[2])
        # Opener may still sit inside the finished callsign — search again after it.
        if opener is not None and opener[0] < signoff[1] - 0.05:
            opener = best_opener(
                words, next_seg, not_before=signoff[1] - 0.05
            )

    # Both landmarks found (including Whisper-abutted sign-off/opener).
    if signoff is not None and opener is not None and opener[0] >= signoff[1] - 0.15:
        signoff_end, opener_start = signoff[1], opener[0]
        gap_len = max(0.0, opener_start - signoff_end)

        # Abutting handoff (typical last-story → close): keep full callsign.
        if gap_len <= 0.25:
            cut = cut_after_signoff(energy_frames, signoff_end, opener_start)
            return (
                cut,
                cut,
                f"inspect abut signoff@{signoff_end:.2f} opener@{opener_start:.2f} →{cut:.2f}",
            )

        if allow_dual and (
            gap_len >= LONG_GAP_SEC
            or prev_seg.kind == SegmentKind.INTRO
        ):
            # Long cart/break: end previous just after sign-off; start next just before opener.
            # Intro with a collapsed align gap still needs dual edges from energy.
            if gap_len < MIN_CART_SEC and prev_seg.kind == SegmentKind.INTRO:
                rebuilt = dual_cuts_before_opener(
                    energy_frames,
                    opener_start,
                    not_before=max(0.0, signoff_end - 18.0),
                )
                if rebuilt is not None:
                    cut_a, cut_b = rebuilt
                    return (
                        cut_a,
                        cut_b,
                        (
                            f"inspect dual-rebuild signoff@{signoff_end:.2f} "
                            + f"opener@{opener_start:.2f} →{cut_a:.2f}/{cut_b:.2f}"
                        ),
                    )
            # gap_a: first quiet after sign-off (start of cart).
            gap_a = longest_quiet_run(
                energy_frames,
                signoff_end - 0.05,
                min(signoff_end + 2.5, opener_start - 0.2),
                min_silence_sec=0.08,
            )
            if gap_a is not None:
                cut_a = gap_a[0] + min(0.08, 0.3 * (gap_a[1] - gap_a[0]))
            else:
                cut_a = cut_between_landmarks(
                    energy_frames, signoff_end, signoff_end + 0.8
                )
            cut_b = cut_before_opener(
                energy_frames,
                opener_start,
                not_before=signoff_end + 0.3,
                lookback=1.5,
            )
            if cut_b < cut_a + 0.15:
                # Pull cut_a earlier rather than pushing cut_b past the opener.
                cut_a = min(cut_a, cut_b - 0.15)
                cut_a = max(cut_a, signoff_end)
            cut_b = clamp_before_opener(
                cut_b, opener_start, not_before=cut_a
            )
            if prev_seg.kind == SegmentKind.INTRO:
                cut_b = snap_past_sting_callsign(
                    energy_frames,
                    cut_b,
                    opener_start,
                    not_before=cut_a,
                )
            if cut_b - cut_a >= MIN_CART_SEC * 0.5 or gap_len >= LONG_GAP_SEC:
                return (
                    cut_a,
                    cut_b,
                    (
                        f"inspect dual signoff@{signoff_end:.2f} "
                        + f"opener@{opener_start:.2f} →{cut_a:.2f}/{cut_b:.2f}"
                    ),
                )

        cut = cut_between_landmarks(energy_frames, signoff_end, opener_start)
        cut = clamp_before_opener(cut, opener_start, not_before=signoff_end)
        return (
            cut,
            cut,
            f"inspect signoff@{signoff_end:.2f} opener@{opener_start:.2f} →{cut:.2f}",
        )

    # Opener only: cut in quiet immediately before it (not mid-callsign dips).
    if opener is not None:
        opener_start = opener[0]
        floor = max(0.0, edge_a - 0.5)
        cut_b = cut_before_opener(
            energy_frames,
            opener_start,
            not_before=floor,
            lookback=1.2,
        )
        if allow_dual:
            cut_a = edge_a
            collapsed = (
                opener_start - cut_a < MIN_CART_SEC
                or cut_b < cut_a + 0.15
                or cut_b >= opener_start - 0.02
            )
            if collapsed:
                rebuilt = dual_cuts_before_opener(
                    energy_frames,
                    opener_start,
                    not_before=max(0.0, floor - 12.0),
                )
                if rebuilt is not None:
                    cut_a, cut_b = rebuilt
                    return (
                        cut_a,
                        cut_b,
                        (
                            f"inspect opener@{opener_start:.2f} "
                            + f"→rebuild/{cut_a:.2f}/{cut_b:.2f}"
                        ),
                    )
                # Last resort: keep dual but never past opener.
                cut_b = clamp_before_opener(
                    cut_b, opener_start, not_before=floor
                )
                if cut_b < cut_a + 0.15:
                    cut_a = max(floor, cut_b - 0.5)
            else:
                cut_b = max(cut_b, cut_a + 0.15)
                cut_b = clamp_before_opener(
                    cut_b, opener_start, not_before=cut_a
                )
            if prev_seg.kind == SegmentKind.INTRO:
                cut_b = snap_past_sting_callsign(
                    energy_frames,
                    cut_b,
                    opener_start,
                    not_before=cut_a,
                )
            return (
                cut_a,
                cut_b,
                f"inspect opener@{opener_start:.2f} →keep/{cut_b:.2f}",
            )
        if prev_seg.kind == SegmentKind.INTRO:
            cut_b = snap_past_sting_callsign(
                energy_frames,
                cut_b,
                opener_start,
                not_before=floor,
            )
        return cut_b, cut_b, f"inspect opener@{opener_start:.2f} →{cut_b:.2f}"

    # Sign-off only: cut in the first quiet after extended speech.
    if signoff is not None:
        signoff_end = signoff[1]
        gap = longest_quiet_run(
            energy_frames, signoff_end - 0.02, min(t1, signoff_end + 1.2), min_silence_sec=0.06
        )
        cut_a = (
            cut_in_silence_gap(gap[0], gap[1])
            if gap is not None
            else cut_between_landmarks(
                energy_frames, signoff_end, min(t1, signoff_end + 1.2)
            )
        )
        if allow_dual:
            cut_b = max(cut_a + 0.15, edge_b)
            return (
                cut_a,
                cut_b,
                f"inspect signoff@{signoff_end:.2f} →{cut_a:.2f}/keep",
            )
        return cut_a, cut_a, f"inspect signoff@{signoff_end:.2f} →{cut_a:.2f}"

    # Neither matched: keep provisional unless a clearly longer quiet sits nearby.
    gap = longest_quiet_run(
        energy_frames,
        provisional_cut - 0.9,
        provisional_cut + 0.9,
        min_silence_sec=0.12,
    )
    if gap is not None and gap[1] - gap[0] >= 0.18:
        cut = cut_in_silence_gap(gap[0], gap[1])
        if abs(cut - provisional_cut) >= 0.08:
            return cut, cut, f"inspect quiet-run {gap[0]:.2f}-{gap[1]:.2f} →{cut:.2f}"

    return provisional_cut, provisional_cut, "inspect: no landmarks; keep provisional"


def _handoff_needs_asr_inspect(seg_a: Segment, seg_b: Segment) -> bool:
    """
    Free-ASR handoff rewrite is for cart/beep/close neighborhoods only.

    Story→story cuts stay on forced-align script spans + RMS quiet. Hunting
    sign-offs/callsigns there overrides good quiet cuts (e.g. mid-callsign).
    Unscripted residual tails (spoken callsign missing from ``.txt``) are
    handled separately by :func:`_refine_story_story_residual_before_opener`.
    """
    return not (
        seg_a.kind == SegmentKind.STORY and seg_b.kind == SegmentKind.STORY
    )


def _refine_story_story_residual_before_opener(
    audio: Path,
    energy_frames: list[tuple[float, float]],
    provisional_cut: float,
    next_seg: Segment,
    *,
    audio_duration: float,
    asr_model: str = "base.en",
) -> tuple[float, str] | None:
    """
    Story→story only: keep forced-align quiet cuts unless residual speech
    sits between that cut and the next story's *scripted* opener.

    Typical case: reporter speaks an unscripted callsign after the last
    body line; forced-align parks ``start_b`` on that callsign and the
    quiet before it becomes the cut. Locate the next opener from ``.txt``
    and move the cut into the quiet immediately before it.

    Requires a real quiet abutting the opener *and* loud residual energy
    after the provisional cut — avoids chasing a late partial opener match
    (e.g. ``night of nights`` skipping a spoken ``in california`` lead-in).
    """
    # Look far enough left to hear the full residual + opener lead-in;
    # a ±2s window starting mid-callsign often mistimes the next opener.
    t0 = max(0.0, provisional_cut - 4.0)
    t1 = min(audio_duration, provisional_cut + 8.0)
    if t1 - t0 < 1.0:
        return None
    words = transcribe_handoff_window(audio, t0, t1, model_name=asr_model)
    # Ignore false-early opener matches inside residual speech before the cut.
    opener = best_opener(words, next_seg, not_before=provisional_cut - 0.15)
    if opener is None:
        return None
    opener_start = opener[0]
    # Opener already at/near the quiet cut — nothing residual to reclaim.
    if opener_start < provisional_cut + 0.45:
        return None
    # Bound chase so a late false match deeper in the next story cannot pull.
    if opener_start > provisional_cut + 6.0:
        return None
    lookback = min(2.5, max(0.4, opener_start - provisional_cut + 0.3))
    gap = quiet_just_before(
        energy_frames,
        opener_start,
        lookback=lookback,
        min_silence_sec=0.12,
    )
    if gap is None or gap[0] < provisional_cut - 0.05:
        return None
    # Residual callsign island: loud energy between provisional and that quiet.
    residual_lo = provisional_cut + 0.05
    residual_hi = gap[0] - 0.02
    if residual_hi - residual_lo < 0.35:
        return None
    loud_sec = sum(
        0.01
        for tt, r in energy_frames
        if residual_lo <= tt <= residual_hi and r >= 800.0
    )
    if loud_sec < 0.40:
        return None
    cut = cut_in_silence_gap(gap[0], gap[1])
    cut = clamp_before_opener(
        cut, opener_start, not_before=provisional_cut - 0.1
    )
    if cut < provisional_cut + 0.25:
        return None
    return (
        cut,
        f"story residual opener@{opener_start:.2f} →{cut:.2f}",
    )


def refine_boundaries_by_inspection(
    boundaries: list[Boundary],
    segments: tuple[Segment, ...],
    audio: Path,
    energy_frames: list[tuple[float, float]],
    audio_duration: float,
    *,
    asr_model: str = "base.en",
) -> list[Boundary]:
    """
    Inspect intro/break/close handoffs; keep story→story on script+quiet cuts
    except when residual unscripted speech precedes the next opener.

    For cart/beep regions, ASR locates landmarks and energy places the cut.
    Adjacent stories normally keep the quiet cut from ``build_forced_boundaries``.
    """
    from app.newsline.engine.progress import PhaseTimer, log, progress

    seg_by = {s.index: s for s in segments}
    out = list(boundaries)
    inspect = PhaseTimer(
        f"  inspecting handoffs (local ASR={asr_model} + energy)"
    )
    pair_num = 0

    def _apply_cut_pair(
        i_a: int,
        i_b: int,
        cut_a: float,
        cut_b: float,
        detail: str,
    ) -> None:
        nonlocal out
        a = out[i_a]
        b = out[i_b]
        cut_a = max(a.start + 0.3, min(cut_a, audio_duration - 0.3))
        cut_b = max(cut_a, min(cut_b, audio_duration - 0.3))
        log(
            f"    [{a.segment_index}→{b.segment_index}] "
            + f"{a.end:.2f}/{b.start:.2f} → {cut_a:.2f}/{cut_b:.2f}  ({detail})"
        )
        out[i_a] = Boundary(
            segment_index=a.segment_index,
            title=a.title,
            start=a.start,
            end=cut_a,
            match_score=a.match_score,
            anchor_snippet=detail[:80],
        )
        out[i_b] = Boundary(
            segment_index=b.segment_index,
            title=b.title,
            start=cut_b,
            end=b.end,
            match_score=b.match_score,
            anchor_snippet=b.anchor_snippet,
        )

    def _inspect_pair(i_a: int, i_b: int) -> None:
        nonlocal out, pair_num
        a = out[i_a]
        b = out[i_b]
        seg_a = seg_by[a.segment_index]
        seg_b = seg_by[b.segment_index]
        pair_num += 1
        progress(
            pair_num,
            max(1, len(out) - 1),
            f"handoff {a.segment_index}→{b.segment_index} "
            + f"({seg_a.title[:28]} → {seg_b.title[:28]})",
        )
        provisional = 0.5 * (a.end + b.start)
        half = 8.0 if seg_a.kind == SegmentKind.INTRO else 4.0
        # Dual cuts when billboard/break can own the middle gap (beeps/cart).
        allow_dual = (
            seg_a.kind in (SegmentKind.INTRO, SegmentKind.BREAK)
            or abs(a.end - b.start) > 1.0
        )
        cut_a, cut_b, detail = refine_handoff_from_inspection(
            audio,
            energy_frames,
            provisional,
            seg_a,
            seg_b,
            audio_duration=audio_duration,
            asr_model=asr_model,
            half_window=half,
            allow_dual=allow_dual,
            keep_end=a.end,
            keep_start=b.start,
        )
        # Don't restore a mid-beep provisional start after post-beep / dual landmark cuts.
        if (
            abs(a.end - b.start) > 0.08
            and abs(cut_b - cut_a) <= 0.08
            and "post-beep" not in detail
            and "inspect dual" not in detail
        ):
            # Had a dual gap; inspection collapsed — keep dual edges unless
            # landmarks clearly want a single cut closer to one side.
            cut_a = min(cut_a, a.end)
            cut_b = max(cut_b, b.start)
            cut_b = max(cut_b, cut_a + 0.1)
        _apply_cut_pair(i_a, i_b, cut_a, cut_b, detail)

    def _inspect_story_story_residual(i_a: int, i_b: int) -> None:
        nonlocal out, pair_num
        a = out[i_a]
        b = out[i_b]
        seg_b = seg_by[b.segment_index]
        pair_num += 1
        progress(
            pair_num,
            max(1, len(out) - 1),
            f"handoff {a.segment_index}→{b.segment_index} "
            + f"(story residual check)",
        )
        provisional = 0.5 * (a.end + b.start)
        hit = _refine_story_story_residual_before_opener(
            audio,
            energy_frames,
            provisional,
            seg_b,
            audio_duration=audio_duration,
            asr_model=asr_model,
        )
        if hit is None:
            log(
                f"    [{a.segment_index}→{b.segment_index}] "
                + f"{a.end:.2f}/{b.start:.2f} keep quiet cut"
            )
            return
        cut, detail = hit
        _apply_cut_pair(i_a, i_b, cut, cut, detail)

    for i in range(len(out) - 1):
        a = out[i]
        b = out[i + 1]
        seg_a = seg_by.get(a.segment_index)
        seg_b = seg_by.get(b.segment_index)
        if seg_a is None or seg_b is None:
            continue

        # Skip over billboard: inspect intro ↔ first story instead.
        if seg_b.kind == SegmentKind.BILLBOARD and i + 2 < len(out):
            seg_c = seg_by.get(out[i + 2].segment_index)
            if seg_a.kind == SegmentKind.INTRO and seg_c is not None:
                _inspect_pair(i, i + 2)
            continue
        if seg_a.kind == SegmentKind.BILLBOARD:
            continue

        if seg_a.kind not in (
            SegmentKind.INTRO,
            SegmentKind.STORY,
            SegmentKind.BREAK,
            SegmentKind.CLOSE,
        ):
            continue
        if seg_b.kind not in (
            SegmentKind.STORY,
            SegmentKind.BREAK,
            SegmentKind.CLOSE,
        ):
            continue
        if not _handoff_needs_asr_inspect(seg_a, seg_b):
            if (
                seg_a.kind == SegmentKind.STORY
                and seg_b.kind == SegmentKind.STORY
            ):
                _inspect_story_story_residual(i, i + 1)
            continue

        _inspect_pair(i, i + 1)

    # Re-sync intro → billboard → first story if present.
    for i, b in enumerate(out):
        seg = seg_by.get(b.segment_index)
        if seg is None or seg.kind != SegmentKind.BILLBOARD or i == 0 or i + 1 >= len(out):
            continue
        prev, nxt = out[i - 1], out[i + 1]
        cart_start, cart_end = prev.end, max(prev.end, nxt.start)
        if cart_end - cart_start < MIN_CART_SEC:
            # Align/inspect collapsed the cart — rebuild from energy before first story.
            rebuilt = dual_cuts_before_opener(
                energy_frames,
                nxt.start + 0.05,
                not_before=max(0.0, prev.start + 8.0),
            )
            if rebuilt is not None:
                cart_start, cart_end = rebuilt
                log(
                    f"    billboard rebuild {prev.end:.2f}/{nxt.start:.2f} "
                    + f"→ {cart_start:.2f}/{cart_end:.2f}"
                )
                out[i + 1] = Boundary(
                    segment_index=nxt.segment_index,
                    title=nxt.title,
                    start=cart_end,
                    end=nxt.end,
                    match_score=nxt.match_score,
                    anchor_snippet=nxt.anchor_snippet,
                )
        out[i] = Boundary(
            segment_index=b.segment_index,
            title=b.title,
            start=cart_start,
            end=max(cart_start, cart_end),
            match_score=100.0,
            anchor_snippet="billboard (quiet gap)",
        )
        out[i - 1] = Boundary(
            segment_index=prev.segment_index,
            title=prev.title,
            start=prev.start,
            end=out[i].start,
            match_score=prev.match_score,
            anchor_snippet=prev.anchor_snippet,
        )

    if out:
        last = out[-1]
        out[-1] = Boundary(
            segment_index=last.segment_index,
            title=last.title,
            start=last.start,
            end=audio_duration,
            match_score=last.match_score,
            anchor_snippet=last.anchor_snippet,
        )
    _ = inspect.done(f"{pair_num} handoffs refined")
    return out
