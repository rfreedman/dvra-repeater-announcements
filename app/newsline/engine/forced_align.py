from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.newsline.engine.align import normalize_tokens
from app.newsline.engine.models import Boundary, Segment, SegmentKind, WordTiming
from app.newsline.engine.silence_cuts import (
    dual_quiet_cuts_for_long_gap,
    extend_through_speech,
    find_quiet_cut,
    handoff_search_window,
    load_mono_pcm16,
    quiet_runs,
    rms_frames,
)
from app.newsline.engine.transcript import export_boundaries


def _script_blocks(
    segments: tuple[Segment, ...],
) -> list[tuple[Segment, list[str]]]:
    """Speakable blocks in order (excludes billboard / empty)."""
    blocks: list[tuple[Segment, list[str]]] = []
    for seg in segments:
        if seg.kind == SegmentKind.BILLBOARD:
            continue
        toks = normalize_tokens(seg.speakable_text)
        if toks:
            blocks.append((seg, toks))
    return blocks


def _cache_key(audio: Path, segments: tuple[Segment, ...], model_name: str) -> str:
    h = hashlib.sha256()
    h.update(model_name.encode())
    h.update(str(audio.resolve()).encode())
    try:
        h.update(str(audio.stat().st_mtime_ns).encode())
        h.update(str(audio.stat().st_size).encode())
    except OSError:
        pass
    for seg, toks in _script_blocks(segments):
        h.update(f"{seg.index}:{' '.join(toks)}".encode())
    return h.hexdigest()[:24]


def _cache_path(cache_dir: Path, audio: Path, segments: tuple[Segment, ...], model_name: str) -> Path:
    key = _cache_key(audio, segments, model_name)
    safe_model = model_name.replace(".", "_").replace("/", "_")
    return cache_dir / "forced_align" / f"{audio.stem}_{safe_model}_{key}.json"


def align_script_words(
    audio: Path,
    segments: tuple[Segment, ...],
    *,
    model_name: str = "base.en",
    cache_dir: Path | None = None,
) -> tuple[list[WordTiming], list[tuple[int, float, float, str]]]:
    """
    Force-align official speakable script to audio via stable-ts.

    Returns (flat word timings, per-speakable-block spans as
    (segment_index, start, end, snippet)).
    """
    blocks = _script_blocks(segments)
    if not blocks:
        return [], []

    from app.newsline.engine.progress import PhaseTimer, log

    cache_path: Path | None = None
    if cache_dir is not None:
        cache_path = _cache_path(cache_dir, audio, segments, model_name)
        if cache_path.is_file():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            words = [
                WordTiming(word=w["word"], start=float(w["start"]), end=float(w["end"]))
                for w in payload["words"]
            ]
            spans = [
                (int(s["segment_index"]), float(s["start"]), float(s["end"]), s["snippet"])
                for s in payload["spans"]
            ]
            log(f"  forced-align cache hit ({cache_path.name})")
            return words, spans

    import stable_whisper

    align = PhaseTimer(
        f"  stable-ts align ({model_name}, {len(blocks)} blocks) — this can take several minutes"
    )
    text = "\n".join(" ".join(toks) for _, toks in blocks)
    model = stable_whisper.load_model(model_name)
    result = model.align(
        str(audio),
        text,
        language="en",
        original_split=True,
        suppress_silence=True,
        suppress_word_ts=True,
        verbose=False,
    )
    align.done()

    if len(result.segments) != len(blocks):
        raise RuntimeError(
            f"forced align segment count mismatch: got {len(result.segments)}, "
            f"expected {len(blocks)}"
        )

    words: list[WordTiming] = []
    spans: list[tuple[int, float, float, str]] = []
    for (seg, _toks), rseg in zip(blocks, result.segments, strict=False):
        rwords = list(rseg.words or [])
        if not rwords:
            raise RuntimeError(
                f"forced align produced no words for segment {seg.index} ({seg.title})"
            )
        for w in rwords:
            words.append(
                WordTiming(
                    word=str(w.word).strip(),
                    start=float(w.start),
                    end=float(w.end),
                )
            )
        snippet = " ".join(str(w.word).strip() for w in rwords[:6])
        spans.append((seg.index, float(rwords[0].start), float(rwords[-1].end), snippet))

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "words": [{"word": w.word, "start": w.start, "end": w.end} for w in words],
            "spans": [
                {
                    "segment_index": si,
                    "start": st,
                    "end": en,
                    "snippet": sn,
                }
                for si, st, en, sn in spans
            ],
        }
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return words, spans


def build_forced_boundaries(
    segments: tuple[Segment, ...],
    spans: list[tuple[int, float, float, str]],
    audio_duration: float,
    *,
    energy_frames: list[tuple[float, float]],
) -> tuple[Boundary, ...]:
    """
    Build boundaries using forced-align landmarks + **empirical quiet cuts**.

    Alignment only nominates a search window between speakable blocks. The cut
    is the midpoint of the longest low-RMS run in real audio (quiet between
    stories). Billboard is the cart gap between intro and the first story cut.
    """
    span_by_index = {si: (st, en, sn) for si, st, en, sn in spans}

    # Speakable landmarks only (intro / story / break / close) — not billboard.
    spoken: list[tuple[Segment, float, float, str]] = []
    for seg in segments:
        if seg.kind == SegmentKind.BILLBOARD:
            continue
        if seg.index not in span_by_index:
            continue
        st, en, sn = span_by_index[seg.index]
        if seg.kind == SegmentKind.INTRO:
            st = 0.0
        spoken.append((seg, st, en, sn))

    if not spoken:
        return ()

    # Quiet cut after each spoken block (before the next). Last has no cut.
    # Long gaps (cart / break) get two cuts: after prev speech, before next speech.
    quiet_after: dict[int, float] = {}
    quiet_before: dict[int, float] = {}
    for i in range(len(spoken) - 1):
        seg_a, _sa, end_a, _sna = spoken[i]
        seg_b, start_b, _eb, _snb = spoken[i + 1]
        # Finish callsign / source-sting tails after the last aligned word,
        # but never walk through a short gap into the next story opener.
        end_a_ext = extend_through_speech(
            energy_frames,
            end_a,
            max_extend=5.0,
            min_quiet_sec=0.25,
            next_speech_start=start_b,
        )
        dual = dual_quiet_cuts_for_long_gap(
            energy_frames, end_a_ext, start_b, audio_duration=audio_duration
        )
        if dual is not None:
            cut_a, cut_b = dual
            quiet_after[seg_a.index] = max(cut_a, end_a_ext)
            quiet_before[seg_b.index] = cut_b
        else:
            # Prefer the quiet between extended prev end and next opener.
            next_landmark = (
                start_b if start_b > end_a_ext + 0.08 else end_a_ext + 0.8
            )
            lo, hi = handoff_search_window(
                end_a_ext, next_landmark, audio_duration=audio_duration
            )
            cut = find_quiet_cut(energy_frames, lo, hi)
            if end_a_ext <= start_b - 0.05:
                # Extension stayed before next opener — keep tail, stop before opener.
                cut = max(cut, end_a_ext)
                cut = min(cut, start_b - 0.02)
            elif end_a_ext <= end_a + 0.05:
                # Abutting (no extension): cut in the tiny gap before opener.
                cut = min(max(cut, end_a - 0.15), start_b - 0.02)
            else:
                # Extension past start_b: only trust it when there is *no* quiet
                # near the next align start (early landmark inside a source sting).
                # If a handoff quiet exists there, cut in that gap instead of
                # swallowing the next opener.
                near_quiet = quiet_runs(
                    energy_frames,
                    max(0.0, start_b - 0.45),
                    start_b + 0.12,
                    min_silence_sec=0.08,
                )
                if near_quiet:
                    gap_lo = max(0.0, end_a - 0.2)
                    gap_hi = min(audio_duration, start_b + 0.35)
                    cut = find_quiet_cut(energy_frames, gap_lo, gap_hi)
                    cut = min(max(cut, end_a - 0.15), start_b - 0.02)
                else:
                    cut = end_a_ext
            quiet_after[seg_a.index] = cut
            quiet_before[seg_b.index] = cut

    # First spoken start (intro forced to 0).
    start_of: dict[int, float] = {}
    for i, (seg, speech_start, _en, _sn) in enumerate(spoken):
        if seg.kind == SegmentKind.INTRO:
            start_of[seg.index] = 0.0
        elif seg.index in quiet_before:
            start_of[seg.index] = quiet_before[seg.index]
        elif i == 0:
            start_of[seg.index] = speech_start
        else:
            prev = spoken[i - 1][0]
            start_of[seg.index] = quiet_after[prev.index]

    boundaries: list[Boundary] = []
    for seg in segments:
        if seg.kind == SegmentKind.BILLBOARD:
            intro = next((s for s in spoken if s[0].kind == SegmentKind.INTRO), None)
            first_story = next(
                (s for s in spoken if s[0].kind == SegmentKind.STORY), None
            )
            if intro is None or first_story is None:
                continue
            b_start = quiet_after.get(intro[0].index, intro[2])
            b_end = start_of.get(first_story[0].index, first_story[1])
            boundaries.append(
                Boundary(
                    segment_index=seg.index,
                    title=seg.title,
                    start=b_start,
                    end=max(b_start, b_end),
                    match_score=100.0,
                    anchor_snippet="billboard (quiet gap)",
                )
            )
            continue

        if seg.index not in start_of:
            prev_end = boundaries[-1].end if boundaries else 0.0
            boundaries.append(
                Boundary(
                    segment_index=seg.index,
                    title=seg.title,
                    start=prev_end,
                    end=prev_end,
                    match_score=0.0,
                    anchor_snippet="no forced-align span",
                )
            )
            continue

        start = start_of[seg.index]
        end = quiet_after.get(seg.index, audio_duration)

        snippet = span_by_index[seg.index][2]
        boundaries.append(
            Boundary(
                segment_index=seg.index,
                title=seg.title,
                start=max(0.0, min(start, audio_duration)),
                end=max(start, min(end, audio_duration)),
                match_score=100.0,
                anchor_snippet=f"quiet-cut←{snippet[:40]}",
            )
        )

    # Re-order by transcript and sync intro end → billboard start.
    by_index = {b.segment_index: b for b in boundaries}
    ordered: list[Boundary] = []
    for seg in segments:
        b = by_index.get(seg.index)
        if b is None:
            continue
        ordered.append(b)

    for i, b in enumerate(ordered):
        if i + 1 >= len(ordered):
            break
        nxt = ordered[i + 1]
        kind = next(s.kind for s in segments if s.index == b.segment_index)
        nxt_kind = next(s.kind for s in segments if s.index == nxt.segment_index)
        if kind == SegmentKind.INTRO and nxt_kind == SegmentKind.BILLBOARD:
            ordered[i] = Boundary(
                segment_index=b.segment_index,
                title=b.title,
                start=b.start,
                end=nxt.start,
                match_score=b.match_score,
                anchor_snippet=b.anchor_snippet,
            )

    if ordered:
        last = ordered[-1]
        ordered[-1] = Boundary(
            segment_index=last.segment_index,
            title=last.title,
            start=last.start,
            end=audio_duration,
            match_score=last.match_score,
            anchor_snippet=last.anchor_snippet,
        )

    return tuple(ordered)


def align_segments_forced(
    segments: tuple[Segment, ...],
    audio: Path,
    audio_duration: float,
    *,
    model_name: str = "base.en",
    cache_dir: Path | None = None,
    inspect_model: str = "base.en",
    energy_frames: list[tuple[float, float]] | None = None,
) -> tuple[tuple[Boundary, ...], list[WordTiming], list[tuple[float, float]]]:
    """Forced-align landmarks → quiet cuts → selective handoff inspect → export.

    Story→story keeps script-span + RMS quiet cuts; residual tails before the
    next scripted opener are fixed in inspection. Free-ASR rewrite runs only
    for intro/break/close neighborhoods (cart, beeps).

    Returns ``(boundaries, words, energy_frames)`` so callers can reuse RMS
    frames in post-export QA without decoding the master again.
    """
    from app.newsline.engine.handoff_inspect import (
        refine_boundaries_by_inspection,
    )
    from app.newsline.engine.progress import PhaseTimer, log

    words, spans = align_script_words(
        audio, segments, model_name=model_name, cache_dir=cache_dir
    )
    log(f"  aligned {len(words)} words across {len(spans)} speakable blocks")
    if energy_frames is None:
        energy = PhaseTimer("  measuring audio energy for quiet-gap cuts")
        samples, rate = load_mono_pcm16(audio)
        frames = rms_frames(samples, rate, frame_ms=10.0)
        energy.done(f"{len(frames)} frames @ {rate} Hz")
    else:
        frames = energy_frames
        log(f"  reusing {len(frames)} energy frames for quiet-gap cuts")
    log("  building provisional boundaries from quiet gaps …")
    all_boundaries = list(
        build_forced_boundaries(
            segments, spans, audio_duration, energy_frames=frames
        )
    )
    all_boundaries = refine_boundaries_by_inspection(
        all_boundaries,
        segments,
        audio,
        frames,
        audio_duration,
        asr_model=inspect_model,
    )
    export = export_boundaries(tuple(all_boundaries), segments, audio_duration)
    return export, words, frames


def validate_forced_coverage(
    segments: tuple[Segment, ...],
    spans: list[tuple[int, float, float, str]] | None,
    boundaries: tuple[Boundary, ...],
) -> list[str]:
    """Fail-loud checks when forced align missed speakable blocks."""

    issues: list[str] = []
    span_ids = {si for si, _, _, _ in (spans or [])}
    for seg in segments:
        if seg.kind in (SegmentKind.BILLBOARD, SegmentKind.BREAK):
            continue
        if normalize_tokens(seg.speakable_text) and seg.index not in span_ids:
            issues.append(f"missing forced-align span for [{seg.index}] {seg.title}")
    for i in range(len(boundaries) - 1):
        a, b = boundaries[i], boundaries[i + 1]
        if b.start + 0.05 < a.start:
            issues.append(
                f"non-monotonic starts: {a.segment_index}@{a.start:.2f} → "
                f"{b.segment_index}@{b.start:.2f}"
            )
    return issues
