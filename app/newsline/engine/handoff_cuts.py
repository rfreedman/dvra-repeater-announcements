"""Place quiet cuts from energy once sign-off and opener times are known."""

from __future__ import annotations

from app.newsline.engine.silence_cuts import (
    cut_in_silence_gap,
    extend_through_speech,
    quiet_runs,
)

# Cart / location-sting gaps between intro and first story are often multi-second.
LONG_GAP_SEC = 1.5
# Billboard / cart span below this is treated as collapsed (peers are ~12–15s).
MIN_CART_SEC = 3.0


def quiet_just_before(
    energy_frames: list[tuple[float, float]],
    target: float,
    *,
    lookback: float = 1.2,
    min_silence_sec: float = 0.10,
) -> tuple[float, float] | None:
    """
    Substantial quiet run ending near ``target``.

    Ignores tiny mid-callsign dips; requires the run to end within 0.25s of target.
    """
    runs = quiet_runs(
        energy_frames,
        target - lookback,
        target + 0.05,
        min_silence_sec=min_silence_sec,
    )
    near = [g for g in runs if target - 0.25 <= g[1] <= target + 0.08]
    if not near:
        return None
    return max(near, key=lambda g: (g[1] - g[0], g[1]))


def _rms_at(energy_frames: list[tuple[float, float]], t: float) -> float:
    if not energy_frames:
        return 0.0
    best = min(energy_frames, key=lambda fr: abs(fr[0] - t))
    return float(best[1])


def _loud_near(
    energy_frames: list[tuple[float, float]],
    t: float,
    *,
    before: float = 0.55,
    after: float = 0.2,
    thr: float = 1500.0,
) -> bool:
    """True if loud frames sit in ``[t-before, t+after]`` (callsign still ringing)."""
    lo, hi = t - before, t + after
    return any(lo <= tt <= hi and r >= thr for tt, r in energy_frames)


def clamp_before_opener(
    cut: float, opener_start: float, *, not_before: float = 0.0
) -> float:
    """Hard invariant: cut must land strictly before the next opener."""
    limit = opener_start - 0.04
    cut = min(cut, limit)
    return max(not_before + 0.02, cut)


def snap_past_sting_callsign(
    energy_frames: list[tuple[float, float]],
    cut_b: float,
    opener_start: float,
    *,
    not_before: float,
    max_advance: float = 1.25,
) -> float:
    """
    Intro→story: if ``cut_b`` lands mid location-sting callsign (false-early
    opener), advance into the quiet island before the real story speech.

    Allows a small advance past ASR ``opener_start`` when energy shows the
    callsign continuing through that time (Report 2552 WD9GCO @ ~46.9 with
    opener ASR @ 46.8). Caps advance so we do not eat into the story.
    """
    if not energy_frames:
        return cut_b
    loud_here = _loud_near(
        energy_frames, cut_b, before=0.12, after=0.20, thr=1500.0
    ) or _rms_at(energy_frames, cut_b) > 1500.0
    if not loud_here:
        return cut_b

    search_hi = opener_start + max_advance
    runs = quiet_runs(
        energy_frames,
        max(not_before, cut_b - 0.05),
        search_hi + 0.35,
        min_silence_sec=0.10,
    )
    after = [g for g in runs if g[0] >= cut_b - 0.08 and g[0] <= search_hi]
    if not after:
        return cut_b
    gap = min(after, key=lambda g: g[0])
    snapped = cut_in_silence_gap(gap[0], gap[1])
    return max(cut_b, min(snapped, search_hi))


def cut_before_opener(
    energy_frames: list[tuple[float, float]],
    opener_start: float,
    *,
    not_before: float = 0.0,
    lookback: float = 1.5,
) -> float:
    """
    Cut in quiet immediately before the next opener.

    When Whisper glues the opener onto the end of a cart/story callsign
    (opener_start lands in a mid-callsign dip or just after), prefer quiet
    *before* the opener. Never return a cut at or after ``opener_start``.
    """
    fallback = clamp_before_opener(
        opener_start - 0.04, opener_start, not_before=not_before
    )
    # Quiet ending within 0.25s of the opener is the real handoff gap.
    # Do not veto it just because the preceding callsign is still loud in a
    # wider lookback (that is normal for abutting story→break gaps and was
    # clipping mid-callsign, e.g. KC0|DGY on 2544_07).
    gap = quiet_just_before(
        energy_frames, opener_start, lookback=lookback, min_silence_sec=0.10
    )
    if gap is not None and gap[0] >= not_before - 0.05:
        return clamp_before_opener(
            cut_in_silence_gap(gap[0], gap[1]), opener_start, not_before=not_before
        )

    # No quiet abutting opener: Whisper may have glued opener onto callsign —
    # finish residual speech, then take the latest quiet still before opener.
    glued = _loud_near(energy_frames, opener_start, before=0.55, after=0.35)
    if glued or _rms_at(energy_frames, opener_start) > 1500.0:
        probe = max(not_before, opener_start - 0.45)
        extended = extend_through_speech(
            energy_frames, probe, max_extend=min(2.2, opener_start - probe), min_quiet_sec=0.14
        )
        extended = min(extended, opener_start - 0.05)
        before = quiet_runs(
            energy_frames,
            max(not_before, extended - 0.8),
            opener_start,
            min_silence_sec=0.10,
        )
        before = [
            g
            for g in before
            if g[0] >= not_before - 0.05 and g[0] < opener_start - 0.04
        ]
        if before:
            # Prefer the quiet run closest to the opener (ends latest).
            gap_b = max(before, key=lambda g: g[1])
            return clamp_before_opener(
                cut_in_silence_gap(gap_b[0], gap_b[1]),
                opener_start,
                not_before=not_before,
            )

    return fallback


def dual_cuts_before_opener(
    energy_frames: list[tuple[float, float]],
    opener_start: float,
    *,
    not_before: float,
) -> tuple[float, float] | None:
    """
    Rebuild intro→story dual edges when the cart gap collapsed.

    ``cut_b`` sits just before the opener; ``cut_a`` at the start of a long
    quiet/cart island before that opener. Returns None if no long quiet exists.
    """
    cut_b = cut_before_opener(
        energy_frames,
        opener_start,
        not_before=not_before,
        lookback=1.5,
    )
    search_lo = max(not_before, opener_start - 22.0)
    search_hi = max(search_lo + 0.5, opener_start - 0.12)
    runs = quiet_runs(
        energy_frames, search_lo, search_hi, min_silence_sec=0.35
    )
    near = [
        g
        for g in runs
        if g[1] >= opener_start - 2.5 and (g[1] - g[0]) >= MIN_CART_SEC
    ]
    if not near:
        near = [g for g in runs if (g[1] - g[0]) >= MIN_CART_SEC]
    if not near:
        return None
    gap = max(near, key=lambda g: (g[1] - g[0], g[1]))
    cut_a = gap[0] + min(0.08, 0.25 * (gap[1] - gap[0]))
    cut_b = min(cut_b, opener_start - 0.04, gap[1] - 0.02)
    cut_b = max(cut_b, cut_a + MIN_CART_SEC * 0.5)
    cut_b = clamp_before_opener(cut_b, opener_start, not_before=cut_a)
    cut_b = snap_past_sting_callsign(
        energy_frames, cut_b, opener_start, not_before=cut_a
    )
    if cut_b - cut_a < MIN_CART_SEC * 0.6:
        return None
    return cut_a, cut_b


def cut_after_signoff(
    energy_frames: list[tuple[float, float]],
    signoff_end: float,
    opener_start: float,
) -> float:
    """
    Cut after the full callsign/tail, never before ``signoff_end``.

    Used when Whisper abuts or overlaps sign-off and opener word times — the
    acoustic callsign often continues a few hundred ms past the word ``end``.
    """
    hi = max(opener_start, signoff_end) + 0.6
    runs = quiet_runs(
        energy_frames,
        signoff_end - 0.05,
        hi,
        min_silence_sec=0.08,
    )
    # First quiet that begins at/after the sign-off (post-callsign gap).
    after = [g for g in runs if g[0] >= signoff_end - 0.06]
    if after:
        gap = after[0]
        return cut_in_silence_gap(gap[0], gap[1])
    # No measured quiet: keep the entire sign-off; slight bleed into close
    # is better than clipping the callsign.
    return signoff_end + 0.04


def cut_between_landmarks(
    energy_frames: list[tuple[float, float]],
    signoff_end: float,
    opener_start: float,
) -> float:
    """Place cut in quiet between landmarks — always the gap just before opener."""
    # Abutting / overlapped Whisper times: never cut inside the callsign.
    if opener_start <= signoff_end + 0.2:
        return cut_after_signoff(energy_frames, signoff_end, opener_start)

    if opener_start <= signoff_end + 0.03:
        return cut_after_signoff(energy_frames, signoff_end, opener_start)

    # Real gap: quiet abutting opener; if ASR glued opener onto a callsign, advance.
    return cut_before_opener(
        energy_frames,
        opener_start,
        not_before=signoff_end,
        lookback=min(2.0, max(0.3, opener_start - signoff_end)),
    )
