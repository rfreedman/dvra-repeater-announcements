from __future__ import annotations

import re
from pathlib import Path

from rapidfuzz import fuzz

from app.newsline.engine.align import (
    _signoff_phrases,
    normalize_token,
    normalize_tokens,
)
from app.newsline.engine.models import Boundary, Segment, SegmentKind, WordTiming
from app.newsline.engine.split import ExportSlot
from app.newsline.engine.verify import ExportBoundaryIssue
from app.newsline.engine.verify_fulltext import (
    _break_boilerplate_in_head,
    _first_line_opener_score,
    _next_story_opener_inside,
    speakable_line_token_lists,
)

_SLICE_NEXT_INSIDE_SCORE = 80.0
_SLICE_MIN_OPENER = 72.0
_MASTER_MATCH_OK = 80.0
_MASTER_WINDOW_SEC = 8.0


def _slice_cache_path(cache_dir: Path, mp3: Path, model_name: str) -> Path:
    safe_model = model_name.replace(".", "_")
    return cache_dir / "slices" / f"{mp3.stem}_{safe_model}.json"


def _cache_valid(cache: Path, mp3: Path) -> bool:
    if not cache.is_file():
        return False
    try:
        return cache.stat().st_mtime >= mp3.stat().st_mtime
    except OSError:
        return False


def transcribe_export_slice(
    mp3: Path,
    *,
    model_name: str,
    cache_dir: Path | None,
    force_full: bool = False,
    model_slot: int = 0,
) -> tuple[list[WordTiming], bool]:
    """
    Word timings for one exported MP3 (0-based), with slice cache.

    Returns ``(words, partial)``. Long slices use head+tail ASR only.
    """
    from app.newsline.engine.transcribe import (
        _load_qa_cache,
        _save_qa_cache,
        transcribe_slice_for_qa,
    )

    if cache_dir is None:
        return transcribe_slice_for_qa(
            mp3,
            model_name=model_name,
            force_full=force_full,
            model_slot=model_slot,
        )
    slice_dir = cache_dir / "slices"
    slice_dir.mkdir(parents=True, exist_ok=True)
    # Separate cache key when head+tail vs full so partial/full never collide.
    cache_path = _slice_cache_path(cache_dir, mp3, model_name)
    if force_full:
        cache_path = cache_path.with_name(cache_path.stem + "_full.json")
    else:
        cache_path = cache_path.with_name(cache_path.stem + "_qa.json")
    if _cache_valid(cache_path, mp3):
        cached = _load_qa_cache(cache_path)
        if cached is not None:
            return cached
    words, partial = transcribe_slice_for_qa(
        mp3,
        model_name=model_name,
        force_full=force_full,
        model_slot=model_slot,
    )
    _save_qa_cache(cache_path, words, partial=partial)
    return words, partial


def _spoken_token_list(words: list[WordTiming]) -> list[str]:
    out: list[str] = []
    for w in words:
        out.extend(normalize_tokens(w.word))
    return out


def _opener_start_in_slice(
    words: list[WordTiming], next_segment: Segment
) -> tuple[float | None, float]:
    """Find next story opener in the last third of slice; return (local_start, score)."""
    if not words:
        return None, 0.0
    spoken = _spoken_token_list(words)
    score, _sample = _next_story_opener_inside(spoken, next_segment)
    if score < _SLICE_NEXT_INSIDE_SCORE:
        return None, score
    lines = speakable_line_token_lists(next_segment)
    if not lines:
        return None, score
    phrase = lines[0][: min(10, len(lines[0]))]
    width = len(phrase)
    if width < 3:
        return None, score
    region_start = (len(spoken) * 2) // 3
    best_i: int | None = None
    best_s = 0.0
    from app.newsline.engine.align import _match_score

    for i in range(region_start, max(region_start, len(spoken) - width + 1)):
        window = spoken[i : i + width]
        s = _match_score(phrase, window)
        if s > best_s:
            best_s = s
            best_i = i
    if best_i is None or best_s < _SLICE_NEXT_INSIDE_SCORE - 5:
        return None, score
    # Map token index back to word time (approximate via cumulative token count).
    tok_i = 0
    for w in words:
        n = len(normalize_tokens(w.word))
        if tok_i + n > best_i:
            return w.start, score
        tok_i += n
    return words[-1].start, score


def _prev_signoff_bleed_in_head(
    words: list[WordTiming], prev_segment: Segment
) -> tuple[bool, str]:
    """Detect previous segment's script tail bleeding into this slice head."""
    phrases = _signoff_phrases(prev_segment)
    if not phrases or not words:
        return False, ""
    head_words = words[: min(16, len(words))]
    head_tokens = _spoken_token_list(head_words)
    head_str = " ".join(head_tokens[:18])
    for phrase in phrases:
        tail = phrase[-min(5, len(phrase)) :]
        tail_str = " ".join(tail)
        score = float(fuzz.token_set_ratio(tail_str, head_str))
        if score >= 78.0:
            return True, tail_str
        # Last two script tokens often bleed alone when the cut is late.
        if len(tail) >= 2:
            pair = " ".join(tail[-2:])
            if float(fuzz.partial_ratio(pair, head_str)) >= 85.0:
                return True, pair
        last = phrase[-1]
        if len(last) >= 3 and last in head_tokens[:8]:
            return True, last
    return False, ""


def _opener_local_time_in_head(
    words: list[WordTiming], next_segment: Segment | None
) -> float | None:
    """Local start of this story's opener inside the slice head (skip bleed)."""
    if not words:
        return None
    lines = speakable_line_token_lists(next_segment) if next_segment else []
    if not lines:
        return words[0].start
    phrase = lines[0][: min(6, len(lines[0]))]
    if not phrase:
        return words[0].start
    tokens, owners = [], []
    for i, w in enumerate(words[:20]):
        for tok in normalize_tokens(w.word):
            tokens.append(tok)
            owners.append(i)
    width = len(phrase)
    if width > len(tokens):
        return words[0].start
    from app.newsline.engine.align import _match_score

    best_i: int | None = None
    best_s = 0.0
    for i in range(len(tokens) - width + 1):
        # Prefer matches in the first ~12 tokens (head).
        if owners[i] > 14:
            break
        s = _match_score(phrase, tokens[i : i + width])
        if s > best_s:
            best_s = s
            best_i = i
    if best_i is not None and best_s >= 78.0:
        return words[owners[best_i]].start
    # Fallback: first word whose token matches opener[0] strongly.
    lead = phrase[0]
    for w in words[:14]:
        for tok in normalize_tokens(w.word):
            if fuzz.ratio(tok, lead) >= 85:
                return w.start
    return words[min(3, len(words) - 1)].start


def _master_tokens_in_window(
    full_words: list[WordTiming], t0: float, t1: float
) -> list[str]:
    tokens: list[str] = []
    for w in full_words:
        if t0 <= w.start < t1:
            tokens.extend(normalize_tokens(w.word))
    return tokens


def _token_window_ratio(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(" ".join(a), " ".join(b)))


def _issues_for_one_slice(
    boundary: Boundary,
    segment: Segment,
    words: list[WordTiming],
    *,
    prev_segment: Segment | None,
    next_segment: Segment | None,
    full_words: list[WordTiming] | None = None,
) -> list[ExportBoundaryIssue]:
    issues: list[ExportBoundaryIssue] = []
    spoken = _spoken_token_list(words)
    if not spoken:
        issues.append(
            ExportBoundaryIssue(
                boundary.segment_index,
                boundary.title,
                "slice_empty",
                "exported slice has no transcribed words",
            )
        )
        return issues

    slice_head = spoken[:28]
    master_head: list[str] = []
    if full_words and segment.kind in (
        SegmentKind.STORY,
        SegmentKind.CLOSE,
        SegmentKind.INTRO,
    ):
        master_head = _master_tokens_in_window(
            full_words,
            boundary.start,
            boundary.start + _MASTER_WINDOW_SEC,
        )

    opener = _first_line_opener_score(segment, spoken)
    if opener is not None and opener < _SLICE_MIN_OPENER and len(spoken) >= 8:
        # Script opener mismatch is OK when the slice head matches the master
        # at this cut (e.g. unspoken ALL-CAPS headline vs real ANCHOR line).
        master_agrees = (
            len(master_head) >= 6
            and _token_window_ratio(slice_head, master_head) >= _MASTER_MATCH_OK
        )
        if not master_agrees:
            lines = speakable_line_token_lists(segment)
            sample = " ".join(lines[0][:8]) if lines else ""
            issues.append(
                ExportBoundaryIssue(
                    boundary.segment_index,
                    boundary.title,
                    "slice_missing_opener",
                    f"slice head score {opener:.0f}; expected {sample[:50]}; "
                    f"heard {' '.join(spoken[:12])}",
                )
            )

    if prev_segment is not None and segment.kind in (
        SegmentKind.STORY,
        SegmentKind.CLOSE,
    ):
        bleed, sample = _prev_signoff_bleed_in_head(words, prev_segment)
        if bleed:
            # Cut at this story's opener inside the head (after bled callsign).
            opener_t = _opener_local_time_in_head(words, segment)
            if opener_t is None:
                opener_t = words[min(3, len(words) - 1)].start
            global_t = boundary.start + opener_t
            issues.append(
                ExportBoundaryIssue(
                    boundary.segment_index,
                    boundary.title,
                    "slice_prev_signoff_in_head",
                    f"previous story sign-off heard in slice head ({sample[:40]}); "
                    f"slice_t={opener_t:.2f}s global={global_t:.2f}s",
                )
            )

    boilerplate = _break_boilerplate_in_head(spoken)
    if boilerplate and segment.kind == SegmentKind.STORY:
        issues.append(
            ExportBoundaryIssue(
                boundary.segment_index,
                boundary.title,
                "slice_break_in_head",
                f"station break in slice head ({boilerplate[:45]})",
            )
        )

    if (
        next_segment is not None
        and segment.kind == SegmentKind.STORY
        and next_segment.kind in (SegmentKind.STORY, SegmentKind.CLOSE)
    ):
        local_t, inside_score = _opener_start_in_slice(words, next_segment)
        if local_t is not None:
            global_t = boundary.start + local_t
            issues.append(
                ExportBoundaryIssue(
                    boundary.segment_index,
                    boundary.title,
                    "slice_next_in_tail",
                    f"next story opener inside exported file (score {inside_score:.0f}); "
                    f"slice_t={local_t:.2f}s global={global_t:.2f}s",
                )
            )

    return issues


def _tokens_alike(a: str, b: str) -> bool:
    """True when ASR garble is still the same word (ariss↔eris, kc0dgy↔kc0)."""
    if not a or not b:
        return False
    if a == b or fuzz.ratio(a, b) >= 80:
        return True
    # Short callsign-like tokens: Whisper drops/changes a letter often.
    return (
        min(len(a), len(b)) >= 3
        and abs(len(a) - len(b)) <= 2
        and fuzz.partial_ratio(a, b) >= 70
    )


def _opener_lead_clipped_on_master(
    segment: Segment,
    slice_words: list[WordTiming],
    full_words: list[WordTiming],
    boundary_start: float,
) -> float | None:
    """First script word missing from slice head but present on master near cut (ASR smear)."""
    lines = speakable_line_token_lists(segment)
    if not lines or len(lines[0]) < 2:
        return None
    lead = lines[0][: min(4, len(lines[0]))]
    head = _spoken_token_list(slice_words[: min(14, len(slice_words))])
    if not head:
        return None
    t0 = normalize_token(lead[0])
    t1 = normalize_token(lead[1]) if len(lead) > 1 else ""
    # Whisper often smears openers (ARISS→Eris); treat close fuzzy hits as present.
    if any(_tokens_alike(t0, ht) for ht in head[:6]):
        return None
    if t1 and not any(_tokens_alike(t1, ht) for ht in head[:8]):
        return None
    # Already cutting at/before the master lead → not clipped (forced-align vs slice ASR).
    for w in full_words:
        if w.start < boundary_start - 0.65 or w.start > boundary_start + 0.25:
            continue
        wt = normalize_token(w.word)
        if not _tokens_alike(wt, t0):
            continue
        if w.start >= boundary_start - 0.05:
            # Lead starts at or after the cut — slice should include it (or ASR smear).
            return None
        return w.start
    return None


def verify_export_slices(
    output_dir: Path,
    report_id: int,
    boundaries: tuple[Boundary, ...],
    segments: tuple[Segment, ...],
    *,
    model_name: str,
    cache_dir: Path | None,
    full_words: list[WordTiming] | None = None,
    qa_workers: int | None = None,
    repeater_timeout: int = 180,
) -> list[ExportBoundaryIssue]:
    """Transcribe each export MP3 and compare to script (parallel ASR workers)."""
    import queue
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.newsline.engine.progress import log, progress
    from app.newsline.engine.split import plan_export_slots
    from app.newsline.engine.whisper_model import (
        qa_worker_count,
        warm_whisper_slots,
    )

    seg_by_index = {s.index: s for s in segments}
    slots = [
        s
        for s in plan_export_slots(
            output_dir,
            report_id,
            boundaries,
            segments,
            repeater_timeout=repeater_timeout,
        )
        if not s.is_station_id
    ]
    # Billboard has no speakable script — skip ASR (standalone billboard cart).
    qa_slots = []
    for slot in slots:
        seg = seg_by_index.get(slot.primary.segment_index)
        if seg is None:
            continue
        if seg.kind == SegmentKind.BILLBOARD:
            continue
        if not slot.path.is_file() or slot.path.stat().st_size == 0:
            continue
        qa_slots.append(slot)

    if not qa_slots:
        return []

    bound_index = {b.segment_index: i for i, b in enumerate(boundaries)}
    workers = min(qa_worker_count(qa_workers), len(qa_slots))
    log(f"    slice ASR with {workers} Whisper worker(s) …")
    warm_whisper_slots(model_name, workers)

    slot_ids: queue.Queue[int] = queue.Queue()
    for s in range(workers):
        slot_ids.put(s)

    def _prev_next(slot: ExportSlot) -> tuple[Segment | None, Segment | None]:
        last_b = slot.boundaries[-1]
        i = bound_index.get(last_b.segment_index)
        nxt_seg = None
        if i is not None and i + 1 < len(boundaries):
            nxt_seg = seg_by_index.get(boundaries[i + 1].segment_index)
        prev_seg = None
        bi = bound_index.get(slot.primary.segment_index)
        if bi is not None and bi > 0:
            prev_seg = seg_by_index.get(boundaries[bi - 1].segment_index)
        return prev_seg, nxt_seg

    def _one(slot: ExportSlot) -> list[ExportBoundaryIssue]:
        boundary = slot.primary
        seg = seg_by_index[boundary.segment_index]
        model_slot = slot_ids.get()
        try:
            slice_words, _partial = transcribe_export_slice(
                slot.path,
                model_name=model_name,
                cache_dir=cache_dir,
                force_full=seg.kind == SegmentKind.INTRO,
                model_slot=model_slot,
            )
        finally:
            slot_ids.put(model_slot)

        prev_seg, nxt_seg = _prev_next(slot)
        found = _issues_for_one_slice(
            boundary,
            seg,
            slice_words,
            prev_segment=prev_seg,
            next_segment=nxt_seg,
            full_words=full_words,
        )
        if full_words and seg.kind in (SegmentKind.STORY, SegmentKind.CLOSE):
            lead_t = _opener_lead_clipped_on_master(
                seg, slice_words, full_words, boundary.start
            )
            if lead_t is not None:
                found.append(
                    ExportBoundaryIssue(
                        boundary.segment_index,
                        boundary.title,
                        "slice_opener_lead_clipped",
                        f"slice missing opener lead word on master at {lead_t:.2f}s "
                        f"(boundary start {boundary.start:.2f}s)",
                    )
                )
        return found

    issues: list[ExportBoundaryIssue] = []
    total = len(qa_slots)
    done = 0
    if workers == 1:
        for slot in qa_slots:
            done += 1
            progress(
                done,
                total,
                f"QA ASR {slot.path.name} ({slot.title[:40]})",
            )
            issues.extend(_one(slot))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_one, slot): slot for slot in qa_slots}
            for fut in as_completed(futures):
                slot = futures[fut]
                issues.extend(fut.result())
                done += 1
                progress(
                    done,
                    total,
                    f"QA ASR {slot.path.name} ({slot.title[:40]})",
                )

    # Stable order by segment index for logs / corrections.
    issues.sort(key=lambda it: (it.segment_index, it.code))
    return issues


def parse_slice_global_cut(detail: str) -> float | None:
    m = re.search(r"global=([\d.]+)s", detail)
    return float(m.group(1)) if m else None
