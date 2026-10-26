from __future__ import annotations

import itertools
import struct
import subprocess
import tempfile
import wave
from pathlib import Path


def load_mono_pcm16(audio: Path, *, sample_rate: int = 16000) -> tuple[list[int], int]:
    """Decode entire file to mono PCM16 for empirical energy analysis."""
    with tempfile.TemporaryDirectory() as td:
        wav_path = Path(td) / "full.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio),
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                str(wav_path),
            ],
            check=True,
        )
        with wave.open(str(wav_path), "rb") as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2
            rate = w.getframerate()
            raw = w.readframes(w.getnframes())
        samples = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
        return samples, rate


def rms_frames(
    samples: list[int],
    rate: int,
    *,
    frame_ms: float = 10.0,
) -> list[tuple[float, float]]:
    """Return ``(time_sec, rms)`` for each frame from t=0."""
    frame = max(1, int(rate * frame_ms / 1000.0))
    out: list[tuple[float, float]] = []
    for i in range(0, len(samples) - frame, frame):
        chunk = samples[i : i + frame]
        mean_sq = sum(x * x for x in chunk) / len(chunk)
        out.append((i / rate, mean_sq**0.5))
    return out


def _adaptive_thresholds(
    window: list[tuple[float, float]],
) -> tuple[float, float]:
    """Return ``(speech_thr, quiet_thr)`` from local energy distribution."""
    levels = sorted(r for _, r in window)
    if not levels:
        return 1500.0, 400.0
    p20 = levels[max(0, int(len(levels) * 0.20))]
    p50 = levels[len(levels) // 2]
    p80 = levels[min(len(levels) - 1, int(len(levels) * 0.80))]
    quiet_thr = max(300.0, min(900.0, p20 * 1.35 + 50.0))
    speech_thr = max(quiet_thr + 400.0, min(3500.0, 0.45 * p50 + 0.55 * p80))
    return speech_thr, quiet_thr


def quiet_runs(
    frames: list[tuple[float, float]],
    search_lo: float,
    search_hi: float,
    *,
    min_silence_sec: float = 0.08,
    quiet_thr: float | None = None,
) -> list[tuple[float, float]]:
    """Return all ``(quiet_start, quiet_end)`` runs at/below quiet threshold."""
    if search_hi <= search_lo + 0.03:
        return []
    window = [(t, r) for t, r in frames if search_lo <= t <= search_hi]
    if len(window) < 5:
        return []
    if quiet_thr is None:
        _speech, quiet_thr = _adaptive_thresholds(window)
    runs: list[tuple[float, float]] = []
    run_start: float | None = None
    last_t = window[0][0]
    for t, r in window:
        last_t = t
        if r <= quiet_thr:
            if run_start is None:
                run_start = t
        else:
            if run_start is not None and t - run_start >= min_silence_sec:
                runs.append((run_start, t))
            run_start = None
    if run_start is not None and last_t - run_start >= min_silence_sec:
        runs.append((run_start, last_t))
    return runs


def longest_quiet_run(
    frames: list[tuple[float, float]],
    search_lo: float,
    search_hi: float,
    *,
    min_silence_sec: float = 0.08,
) -> tuple[float, float] | None:
    """Longest low-RMS run in ``[search_lo, search_hi]`` (empirical gap)."""
    runs = quiet_runs(
        frames, search_lo, search_hi, min_silence_sec=min_silence_sec
    )
    if not runs:
        return None
    return max(runs, key=lambda g: g[1] - g[0])


def find_silence_gap(
    frames: list[tuple[float, float]],
    search_lo: float,
    search_hi: float,
    *,
    min_silence_sec: float = 0.08,
) -> tuple[float, float] | None:
    """
    Empirically find ``(silence_start, silence_end)`` in the search window.

    Prefers the *longest* quiet run that has speech energy on both sides.
    Tiny mid-callsign dips are ignored when a longer inter-story gap exists.
    """
    if search_hi <= search_lo + 0.03:
        return None
    window = [(t, r) for t, r in frames if search_lo <= t <= search_hi]
    if len(window) < 5:
        return None

    speech_thr, quiet_thr = _adaptive_thresholds(window)
    speech_floor = max(quiet_thr + 150.0, speech_thr * 0.55)

    runs = quiet_runs(
        frames,
        search_lo,
        search_hi,
        min_silence_sec=min_silence_sec,
        quiet_thr=quiet_thr,
    )
    # Keep runs that sit between speech (not leading/trailing pad alone).
    flanked: list[tuple[float, float]] = []
    for q0, q1 in runs:
        has_before = any(r >= speech_floor for t, r in window if t < q0)
        has_after = any(r >= speech_floor for t, r in window if t > q1)
        if has_before and has_after:
            flanked.append((q0, q1))
    candidates = flanked or runs
    if not candidates:
        return None

    # Prefer longest; break ties toward the right (near next onset).
    silence_start, silence_end = max(
        candidates, key=lambda g: (g[1] - g[0], g[0])
    )

    # Extend silence_start forward through residual callsign energy.
    for t, r in window:
        if t < silence_start:
            continue
        if t >= silence_end:
            break
        if r > quiet_thr:
            silence_start = t + 0.01
        else:
            break

    if silence_end <= silence_start + 0.015:
        return None
    return silence_start, silence_end


def cut_in_silence_gap(
    silence_start: float,
    silence_end: float,
    *,
    lead_before_next_sec: float = 0.05,
    min_into_gap_sec: float = 0.02,
) -> float:
    """
    Place cut inside the quiet gap, biased toward the *next* speech onset.

    That keeps trailing callsign energy in the previous file while leaving a
    few tens of ms of silence before the next opener.
    """
    gap = silence_end - silence_start
    # Prefer just before next speech; never before silence_start + min_into_gap.
    cut = silence_end - lead_before_next_sec
    earliest = silence_start + min(min_into_gap_sec, 0.35 * gap)
    latest = silence_end - 0.02
    return max(earliest, min(cut, latest))


def extend_through_speech(
    frames: list[tuple[float, float]],
    t: float,
    *,
    max_extend: float = 2.2,
    quiet_thr: float = 500.0,
    min_quiet_sec: float = 0.35,
    resume_loud_sec: float = 0.55,
    next_speech_start: float | None = None,
) -> float:
    """
    Move ``t`` forward through residual speech (callsign / source-sting tails).

    Stops at the start of a real inter-story quiet. Mid-word dips are ignored
    when loud energy resumes soon after (e.g. the ``P`` in ``LABRE-SP``).

    When ``next_speech_start`` is set, a short quiet followed by speech at/after
    that landmark is treated as the story handoff — do not walk into the next
    opener (abutting story→story gaps are often only ~0.12–0.20s).
    """
    # Align end ≈ next opener: nothing to extend without eating the next story.
    if next_speech_start is not None and next_speech_start <= t + 0.12:
        return t
    hi = t + max_extend
    window = [(tt, r) for tt, r in frames if t - 0.02 <= tt <= hi]
    if not window:
        return t
    run_start: float | None = None
    for i, (tt, r) in enumerate(window):
        if tt < t:
            continue
        if r <= quiet_thr:
            if run_start is None:
                run_start = tt
            elif tt - run_start >= min_quiet_sec:
                return run_start
        else:
            if run_start is not None:
                dip = tt - run_start
                # Completed a full quiet (or landed exactly on the threshold when
                # the next frame went loud) — stop at quiet start.
                if dip >= min_quiet_sec:
                    return run_start
                # Resuming speech is the next story's opener — stop even on a
                # short quiet (abutting handoffs are often ~0.15–0.25s).
                if (
                    next_speech_start is not None
                    and tt >= next_speech_start - 0.25
                ):
                    return run_start
                loud_after = 0.0
                for tt2, r2 in window[i:]:
                    if r2 > quiet_thr + 200:
                        loud_after += 0.01
                        if loud_after >= 0.08:
                            break
                    elif tt2 - tt > resume_loud_sec:
                        break
                if loud_after >= 0.08:
                    # Mid-callsign dip — keep walking.
                    run_start = None
                    continue
                if dip >= 0.12:
                    return run_start
            run_start = None
    return t


def find_quiet_cut(
    frames: list[tuple[float, float]],
    search_lo: float,
    search_hi: float,
    *,
    min_quiet_sec: float = 0.06,
) -> float:
    """
    Empirically place a cut in real silence between talk spurts.

    Uses speech→silence→speech edges from RMS, then cuts just before the
    next speech onset (not the midpoint — midpoints still clipped callsigns).
    """
    gap = find_silence_gap(
        frames, search_lo, search_hi, min_silence_sec=min_quiet_sec
    )
    if gap is not None:
        return cut_in_silence_gap(gap[0], gap[1])

    # Fallback: minimum energy in the window.
    window = [(t, r) for t, r in frames if search_lo <= t <= search_hi]
    if not window:
        return 0.5 * (search_lo + search_hi)
    t_min, _ = min(window, key=lambda tr: tr[1])
    return t_min


def handoff_search_window(
    prev_speech_end: float,
    next_speech_start: float,
    *,
    audio_duration: float,
) -> tuple[float, float]:
    """
    Bound the empirical search using align landmarks only.

    Widen when the aligner collapses the gap (word_end ≈ next_start) so we
    still look at real audio around the handoff.
    """
    mid = 0.5 * (prev_speech_end + next_speech_start)
    if next_speech_start > prev_speech_end + 0.12:
        # Include enough left side to finish soft callsign tails after align end.
        lo = prev_speech_end - 0.25
        hi = next_speech_start + 0.45
    else:
        lo = mid - 1.05
        hi = mid + 1.05
    lo = max(0.0, lo)
    hi = min(audio_duration, max(lo + 0.10, hi))
    return lo, hi


def find_cut_after_tone_beeps(
    frames: list[tuple[float, float]],
    search_lo: float,
    search_hi: float,
    *,
    tone_rms: float | None = None,
    min_tone_run_sec: float = 0.35,
    min_beep_count: int = 2,
    min_quiet_after_sec: float = 0.20,
) -> float | None:
    """
    Station-ID beep trains: cut in the quiet after the *last* sustained tone.

    Align/ASR often glue the next story opener onto the beeps (e.g. a single
    long ``radio`` spanning the whole train). Energy is unambiguous: repeated
    near-max RMS bursts, then quiet, then real speech.

    Beep level varies by episode (~5.5k–12k RMS), so when ``tone_rms`` is omitted
    several thresholds are tried high→low (loud trains first, quieter fallback).
    """
    thresholds: tuple[float, ...]
    if tone_rms is None:
        thresholds = (7000.0, 6000.0, 5500.0, 5000.0)
    else:
        thresholds = (float(tone_rms),)
    for thr in thresholds:
        cut = _find_cut_after_tone_beeps_at(
            frames,
            search_lo,
            search_hi,
            tone_rms=thr,
            min_tone_run_sec=min_tone_run_sec,
            min_beep_count=min_beep_count,
            min_quiet_after_sec=min_quiet_after_sec,
        )
        if cut is not None:
            return cut
    return None


def _find_cut_after_tone_beeps_at(
    frames: list[tuple[float, float]],
    search_lo: float,
    search_hi: float,
    *,
    tone_rms: float,
    min_tone_run_sec: float,
    min_beep_count: int,
    min_quiet_after_sec: float,
) -> float | None:
    if search_hi <= search_lo + 0.5:
        return None
    window = [(t, r) for t, r in frames if search_lo <= t <= search_hi]
    if len(window) < 20:
        return None

    tone_runs: list[tuple[float, float]] = []
    run_start: float | None = None
    for t, r in window:
        if r >= tone_rms:
            if run_start is None:
                run_start = t
        else:
            if run_start is not None and t - run_start >= min_tone_run_sec:
                tone_runs.append((run_start, t))
            run_start = None
    if run_start is not None and window[-1][0] - run_start >= min_tone_run_sec:
        tone_runs.append((run_start, window[-1][0]))

    if len(tone_runs) < min_beep_count:
        return None

    # Cluster runs separated by short gaps (beep train). A long quiet ends the
    # train — later loud story speech must not count as more beeps.
    clusters: list[list[tuple[float, float]]] = []
    cur = [tone_runs[0]]
    for prev, nxt in itertools.pairwise(tone_runs):
        if nxt[0] - prev[1] < 0.55:
            cur.append(nxt)
        else:
            clusters.append(cur)
            cur = [nxt]
    clusters.append(cur)

    # Prefer the latest valid train (closest to the next story).
    for cluster in reversed(clusters):
        if len(cluster) < min_beep_count:
            continue
        last_tone_end = cluster[-1][1]
        quiet_hi = max(search_hi, last_tone_end + 6.0)
        runs = quiet_runs(
            frames,
            last_tone_end - 0.05,
            quiet_hi,
            min_silence_sec=min_quiet_after_sec,
        )
        after = [g for g in runs if g[0] >= last_tone_end - 0.08]
        if not after:
            continue
        gap = after[0]
        # Long rest after the train is unambiguous (beeps → silence → story).
        if gap[1] - gap[0] >= 0.55:
            return cut_in_silence_gap(gap[0], gap[1])
        # Short gap: require sustained energy after (story speech can be as loud
        # as beeps, so do not require RMS < tone_rms).
        post = [(t, r) for t, r in frames if gap[1] <= t <= gap[1] + 2.5]
        speech_like = 0.0
        for _t, r in post:
            if r >= 800.0:
                speech_like += 0.01
                if speech_like >= 0.12:
                    return cut_in_silence_gap(gap[0], gap[1])
            else:
                speech_like = 0.0
    return None


def dual_quiet_cuts_for_long_gap(
    frames: list[tuple[float, float]],
    prev_speech_end: float,
    next_speech_start: float,
    *,
    audio_duration: float,
    long_gap_sec: float = 2.0,
) -> tuple[float, float] | None:
    """
    Long cart/break gaps: cut just after previous speech ends, and just before
    next speech starts (two independent silence-edge detections).
    """
    # Align word-ends often land mid-callsign / before a spoken source sting.
    # Walk energy forward to the real quiet before choosing cut_a.
    prev_speech_end = extend_through_speech(
        frames,
        prev_speech_end,
        max_extend=5.0,
        min_quiet_sec=0.25,
        next_speech_start=next_speech_start,
    )
    if next_speech_start < prev_speech_end + 0.15:
        # Next align landmark sits inside residual prev speech — not a long gap.
        return None
    if next_speech_start - prev_speech_end < long_gap_sec:
        return None

    after_lo = max(0.0, prev_speech_end - 0.05)
    after_hi = min(audio_duration, prev_speech_end + 1.5)
    # Align often lands *inside* the station-ID beep train; search well past it.
    before_lo = max(0.0, prev_speech_end + 0.5)
    before_hi = min(audio_duration, max(next_speech_start + 5.0, prev_speech_end + 8.0))
    if after_hi <= after_lo or before_hi <= before_lo:
        return None

    gap_a = find_silence_gap(frames, after_lo, after_hi)

    if gap_a is not None:
        # After previous: enter silence, stay near silence_start (end of talk).
        cut_a = cut_in_silence_gap(
            gap_a[0], gap_a[1], lead_before_next_sec=gap_a[1] - gap_a[0] - 0.03
        )
        # Prefer early in this local gap (= just after speech ends).
        cut_a = gap_a[0] + min(0.08, 0.25 * (gap_a[1] - gap_a[0]))
    else:
        cut_a = find_quiet_cut(frames, after_lo, after_hi)

    # Prefer post-beep quiet when a tone train is present (station ID).
    beep_cut = find_cut_after_tone_beeps(frames, before_lo, before_hi)
    if beep_cut is not None and beep_cut > cut_a + 0.2:
        cut_b = beep_cut
    else:
        gap_b = find_silence_gap(
            frames,
            max(before_lo, next_speech_start - 2.0),
            min(before_hi, next_speech_start + 1.5),
        )
        if gap_b is not None:
            cut_b = cut_in_silence_gap(gap_b[0], gap_b[1])
        else:
            cut_b = find_quiet_cut(
                frames,
                max(before_lo, next_speech_start - 2.0),
                min(before_hi, next_speech_start + 1.5),
            )

    if cut_b <= cut_a + 0.08:
        return None
    return cut_a, cut_b
