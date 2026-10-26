"""Inspect intro, break, and close handoffs and place quiet cuts from landmarks.

Deferred structure work: this module is about 1,100 lines, and cut/QA code
imports private helpers from ``align`` and ``verify_fulltext``
(``_signoff_phrases``, ``_match_score``, ``_flatten_words``,
``_opening_speech_time``, line matchers). Do not split this file or rename
those helpers until tests cover handoff inspection and quiet-cut placement.
Packing tests are not that coverage. See ``docs/ALIGNMENT_ROADMAP.md``.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from rapidfuzz import fuzz

from app.newsline.engine.align import (
    _signoff_phrases,
    normalize_tokens,
)
from app.newsline.engine.models import Boundary, Segment, SegmentKind, WordTiming
from app.newsline.engine.silence_cuts import (
    cut_in_silence_gap,
    find_cut_after_tone_beeps,
    longest_quiet_run,
    quiet_runs,
)
from app.newsline.engine.silence_cuts import (
    extend_through_speech as _extend_through_speech,
)
from app.newsline.engine.verify_fulltext import speakable_line_token_lists

_MIN_MATCH = 86.0
_MIN_MATCH_LOOSE = 82.0
# Cart / location-sting gaps between intro and first story are often multi-second.
_LONG_GAP_SEC = 1.5
# Billboard / cart span below this is treated as collapsed (peers are ~12–15s).
_MIN_CART_SEC = 3.0


def _ordered_match_score(anchor: list[str], window: list[str]) -> float:
    """Ordered string ratio only — token_set_ratio false-matches shared little words."""
    if not anchor or not window or len(anchor) != len(window):
        return 0.0
    return float(fuzz.ratio(" ".join(anchor), " ".join(window)))


def _opener_phrase(segment: Segment) -> list[str]:
    lines = speakable_line_token_lists(segment)
    if not lines:
        return normalize_tokens(segment.speakable_text)[:8]
    return lines[0][: min(10, len(lines[0]))]


def _signoff_candidates(segment: Segment) -> list[list[str]]:
    """Sign-off / intro-tail phrases from the segment's own script only."""
    return _signoff_phrases(segment)


def _opener_candidates(segment: Segment) -> list[list[str]]:
    base = _opener_phrase(segment)
    out: list[list[str]] = []
    if len(base) >= 4:
        out.append(base)
        out.append(base[:6] if len(base) > 6 else base)
        out.append(base[:4])
        # Beep trains often garble the first word(s); match the stable tail.
        if len(base) >= 6:
            out.append(base[1:7])
            out.append(base[2:8] if len(base) >= 8 else base[2:])
    elif base:
        out.append(base)
    return out


def _decode_window_wav(audio: Path, t0: float, t1: float, out_wav: Path) -> None:
    dur = max(0.1, t1 - t0)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, t0):.3f}",
            "-i",
            str(audio),
            "-t",
            f"{dur:.3f}",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(out_wav),
        ],
        check=True,
    )


def transcribe_handoff_window(
    audio: Path,
    t0: float,
    t1: float,
    *,
    model_name: str = "base.en",
) -> list[WordTiming]:
    """ASR the handoff neighborhood; word times are absolute on the master timeline."""
    from app.newsline.engine.whisper_model import get_whisper_model

    model = get_whisper_model(model_name)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "win.wav"
        _decode_window_wav(audio, t0, t1, wav)
        segments, _info = model.transcribe(
            str(wav),
            word_timestamps=True,
            language="en",
            vad_filter=False,
        )
        words: list[WordTiming] = []
        for seg in segments:
            if not seg.words:
                continue
            for w in seg.words:
                if not w.word or w.start is None or w.end is None:
                    continue
                words.append(
                    WordTiming(
                        word=w.word.strip(),
                        start=t0 + float(w.start),
                        end=t0 + float(w.end),
                    )
                )
        return words


def _flatten(words: list[WordTiming]) -> tuple[list[str], list[int]]:
    tokens: list[str] = []
    owners: list[int] = []
    for i, w in enumerate(words):
        for tok in normalize_tokens(w.word):
            tokens.append(tok)
            owners.append(i)
    return tokens, owners


def _find_phrase_span(
    phrase: list[str],
    words: list[WordTiming],
    *,
    not_before: float | None = None,
    not_after: float | None = None,
    prefer: str = "best",
    min_match: float = _MIN_MATCH,
) -> tuple[float, float, float] | None:
    """Return ``(start, end, score)`` for the best phrase match in ``words``."""
    if not phrase or not words:
        return None
    tokens, owners = _flatten(words)
    width = len(phrase)
    if width > len(tokens):
        return None
    candidates: list[tuple[float, float, float, int]] = []
    for i in range(len(tokens) - width + 1):
        w0 = words[owners[i]]
        if not_before is not None and w0.start < not_before:
            continue
        if not_after is not None and w0.start > not_after:
            continue
        window = tokens[i : i + width]
        # Require the first token to be a strong match (blocks callsign↔opener swaps).
        if fuzz.ratio(phrase[0], window[0]) < 80:
            continue
        score = _ordered_match_score(phrase, window)
        if score < min_match:
            continue
        w1 = words[owners[i + width - 1]]
        candidates.append((w0.start, w1.end, score, i))
    if not candidates:
        return None
    if prefer == "latest":
        candidates.sort(key=lambda c: (c[1], c[2]))
        start, end, score, _ = candidates[-1]
    elif prefer == "earliest":
        candidates.sort(key=lambda c: (c[0], -c[2]))
        start, end, score, _ = candidates[0]
    else:
        candidates.sort(key=lambda c: (c[2], c[1]))
        start, end, score, _ = candidates[-1]
    return start, end, score


def _best_signoff(
    words: list[WordTiming],
    segment: Segment,
    *,
    not_after: float | None = None,
) -> tuple[float, float, float] | None:
    best: tuple[float, float, float] | None = None
    for phrase in _signoff_candidates(segment):
        loose = len(phrase) <= 5
        hit = _find_phrase_span(
            phrase,
            words,
            not_after=not_after,
            prefer="latest",
            min_match=_MIN_MATCH_LOOSE if loose else _MIN_MATCH,
        )
        if hit is None:
            continue
        if best is None or hit[1] > best[1] or (
            abs(hit[1] - best[1]) < 0.05 and hit[2] > best[2]
        ):
            best = hit
    return best


def _best_opener(
    words: list[WordTiming],
    segment: Segment,
    *,
    not_before: float | None = None,
) -> tuple[float, float, float] | None:
    best: tuple[float, float, float] | None = None
    for phrase in _opener_candidates(segment):
        hit = _find_phrase_span(
            phrase,
            words,
            not_before=not_before,
            prefer="earliest",
            min_match=_MIN_MATCH_LOOSE if len(phrase) <= 4 else _MIN_MATCH,
        )
        if hit is None:
            continue
        if best is None or hit[0] < best[0] or (
            abs(hit[0] - best[0]) < 0.05 and hit[2] > best[2]
        ):
            best = hit
    return best


def _quiet_just_before(
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


def _clamp_before_opener(
    cut: float, opener_start: float, *, not_before: float = 0.0
) -> float:
    """Hard invariant: cut must land strictly before the next opener."""
    limit = opener_start - 0.04
    cut = min(cut, limit)
    return max(not_before + 0.02, cut)


def _snap_past_sting_callsign(
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


def _cut_before_opener(
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
    fallback = _clamp_before_opener(
        opener_start - 0.04, opener_start, not_before=not_before
    )
    # Quiet ending within 0.25s of the opener is the real handoff gap.
    # Do not veto it just because the preceding callsign is still loud in a
    # wider lookback (that is normal for abutting story→break gaps and was
    # clipping mid-callsign, e.g. KC0|DGY on 2544_07).
    gap = _quiet_just_before(
        energy_frames, opener_start, lookback=lookback, min_silence_sec=0.10
    )
    if gap is not None and gap[0] >= not_before - 0.05:
        return _clamp_before_opener(
            cut_in_silence_gap(gap[0], gap[1]), opener_start, not_before=not_before
        )

    # No quiet abutting opener: Whisper may have glued opener onto callsign —
    # finish residual speech, then take the latest quiet still before opener.
    glued = _loud_near(energy_frames, opener_start, before=0.55, after=0.35)
    if glued or _rms_at(energy_frames, opener_start) > 1500.0:
        probe = max(not_before, opener_start - 0.45)
        extended = _extend_through_speech(
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
            return _clamp_before_opener(
                cut_in_silence_gap(gap_b[0], gap_b[1]),
                opener_start,
                not_before=not_before,
            )

    return fallback


def _dual_cuts_before_opener(
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
    cut_b = _cut_before_opener(
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
        if g[1] >= opener_start - 2.5 and (g[1] - g[0]) >= _MIN_CART_SEC
    ]
    if not near:
        near = [g for g in runs if (g[1] - g[0]) >= _MIN_CART_SEC]
    if not near:
        return None
    gap = max(near, key=lambda g: (g[1] - g[0], g[1]))
    cut_a = gap[0] + min(0.08, 0.25 * (gap[1] - gap[0]))
    cut_b = min(cut_b, opener_start - 0.04, gap[1] - 0.02)
    cut_b = max(cut_b, cut_a + _MIN_CART_SEC * 0.5)
    cut_b = _clamp_before_opener(cut_b, opener_start, not_before=cut_a)
    cut_b = _snap_past_sting_callsign(
        energy_frames, cut_b, opener_start, not_before=cut_a
    )
    if cut_b - cut_a < _MIN_CART_SEC * 0.6:
        return None
    return cut_a, cut_b


def _cut_after_signoff(
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


def _cut_between_landmarks(
    energy_frames: list[tuple[float, float]],
    signoff_end: float,
    opener_start: float,
) -> float:
    """Place cut in quiet between landmarks — always the gap just before opener."""
    # Abutting / overlapped Whisper times: never cut inside the callsign.
    if opener_start <= signoff_end + 0.2:
        return _cut_after_signoff(energy_frames, signoff_end, opener_start)

    if opener_start <= signoff_end + 0.03:
        return _cut_after_signoff(energy_frames, signoff_end, opener_start)

    # Real gap: quiet abutting opener; if ASR glued opener onto a callsign, advance.
    return _cut_before_opener(
        energy_frames,
        opener_start,
        not_before=signoff_end,
        lookback=min(2.0, max(0.3, opener_start - signoff_end)),
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

    signoff = _best_signoff(words, prev_seg, not_after=provisional_cut + 2.5)
    opener_floor = (signoff[1] - 0.15) if signoff is not None else (provisional_cut - 2.5)
    opener = _best_opener(words, next_seg, not_before=opener_floor)

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
        extended = _extend_through_speech(
            energy_frames,
            signoff[1],
            max_extend=5.0,
            min_quiet_sec=0.25,
            next_speech_start=next_speech,
        )
        signoff = (signoff[0], extended, signoff[2])
        # Opener may still sit inside the finished callsign — search again after it.
        if opener is not None and opener[0] < signoff[1] - 0.05:
            opener = _best_opener(
                words, next_seg, not_before=signoff[1] - 0.05
            )

    # Both landmarks found (including Whisper-abutted sign-off/opener).
    if signoff is not None and opener is not None and opener[0] >= signoff[1] - 0.15:
        signoff_end, opener_start = signoff[1], opener[0]
        gap_len = max(0.0, opener_start - signoff_end)

        # Abutting handoff (typical last-story → close): keep full callsign.
        if gap_len <= 0.25:
            cut = _cut_after_signoff(energy_frames, signoff_end, opener_start)
            return (
                cut,
                cut,
                f"inspect abut signoff@{signoff_end:.2f} opener@{opener_start:.2f} →{cut:.2f}",
            )

        if allow_dual and (
            gap_len >= _LONG_GAP_SEC
            or prev_seg.kind == SegmentKind.INTRO
        ):
            # Long cart/break: end previous just after sign-off; start next just before opener.
            # Intro with a collapsed align gap still needs dual edges from energy.
            if gap_len < _MIN_CART_SEC and prev_seg.kind == SegmentKind.INTRO:
                rebuilt = _dual_cuts_before_opener(
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
                            f"opener@{opener_start:.2f} →{cut_a:.2f}/{cut_b:.2f}"
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
                cut_a = _cut_between_landmarks(
                    energy_frames, signoff_end, signoff_end + 0.8
                )
            cut_b = _cut_before_opener(
                energy_frames,
                opener_start,
                not_before=signoff_end + 0.3,
                lookback=1.5,
            )
            if cut_b < cut_a + 0.15:
                # Pull cut_a earlier rather than pushing cut_b past the opener.
                cut_a = min(cut_a, cut_b - 0.15)
                cut_a = max(cut_a, signoff_end)
            cut_b = _clamp_before_opener(
                cut_b, opener_start, not_before=cut_a
            )
            if prev_seg.kind == SegmentKind.INTRO:
                cut_b = _snap_past_sting_callsign(
                    energy_frames,
                    cut_b,
                    opener_start,
                    not_before=cut_a,
                )
            if cut_b - cut_a >= _MIN_CART_SEC * 0.5 or gap_len >= _LONG_GAP_SEC:
                return (
                    cut_a,
                    cut_b,
                    (
                        f"inspect dual signoff@{signoff_end:.2f} "
                        f"opener@{opener_start:.2f} →{cut_a:.2f}/{cut_b:.2f}"
                    ),
                )

        cut = _cut_between_landmarks(energy_frames, signoff_end, opener_start)
        cut = _clamp_before_opener(cut, opener_start, not_before=signoff_end)
        return (
            cut,
            cut,
            f"inspect signoff@{signoff_end:.2f} opener@{opener_start:.2f} →{cut:.2f}",
        )

    # Opener only: cut in quiet immediately before it (not mid-callsign dips).
    if opener is not None:
        opener_start = opener[0]
        floor = max(0.0, edge_a - 0.5)
        cut_b = _cut_before_opener(
            energy_frames,
            opener_start,
            not_before=floor,
            lookback=1.2,
        )
        if allow_dual:
            cut_a = edge_a
            collapsed = (
                opener_start - cut_a < _MIN_CART_SEC
                or cut_b < cut_a + 0.15
                or cut_b >= opener_start - 0.02
            )
            if collapsed:
                rebuilt = _dual_cuts_before_opener(
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
                            f"→rebuild/{cut_a:.2f}/{cut_b:.2f}"
                        ),
                    )
                # Last resort: keep dual but never past opener.
                cut_b = _clamp_before_opener(
                    cut_b, opener_start, not_before=floor
                )
                if cut_b < cut_a + 0.15:
                    cut_a = max(floor, cut_b - 0.5)
            else:
                cut_b = max(cut_b, cut_a + 0.15)
                cut_b = _clamp_before_opener(
                    cut_b, opener_start, not_before=cut_a
                )
            if prev_seg.kind == SegmentKind.INTRO:
                cut_b = _snap_past_sting_callsign(
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
            cut_b = _snap_past_sting_callsign(
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
            else _cut_between_landmarks(
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
    opener = _best_opener(words, next_seg, not_before=provisional_cut - 0.15)
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
    gap = _quiet_just_before(
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
    cut = _clamp_before_opener(
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
            f"{a.end:.2f}/{b.start:.2f} → {cut_a:.2f}/{cut_b:.2f}  ({detail})"
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
            f"({seg_a.title[:28]} → {seg_b.title[:28]})",
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
            f"(story residual check)",
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
                f"{a.end:.2f}/{b.start:.2f} keep quiet cut"
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
        if cart_end - cart_start < _MIN_CART_SEC:
            # Align/inspect collapsed the cart — rebuild from energy before first story.
            rebuilt = _dual_cuts_before_opener(
                energy_frames,
                nxt.start + 0.05,
                not_before=max(0.0, prev.start + 8.0),
            )
            if rebuilt is not None:
                cart_start, cart_end = rebuilt
                log(
                    f"    billboard rebuild {prev.end:.2f}/{nxt.start:.2f} "
                    f"→ {cart_start:.2f}/{cart_end:.2f}"
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
    inspect.done(f"{pair_num} handoffs refined")
    return out
