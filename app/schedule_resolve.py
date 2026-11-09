"""Which schedule and announcement own each slot, including baseline rotation."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from app.api_types import ScheduleRow
from app.models import Announcement, Schedule, StoreDocument
from app.schedule_match import overlay_matches, pick_overlay
from app.schedule_play import announcement_cannot_play, newsline_blocks_slot
from app.schedule_summary import summarize
from app.schedule_warnings import schedule_warning
from app.slots import Slot, as_local, iter_day_slots, iter_slots, zone_for
from app.spoken_length import blocks_playback, too_long_parts

CONFLICT_HORIZON_DAYS = 70


@dataclass(frozen=True)
class ResolvedSlot:
    slot: Slot
    fire_at: datetime
    schedule: Schedule | None
    announcement: Announcement | None
    source: str
    warning: str | None = None
    skipped_detail: str | None = None

    @property
    def label(self) -> str:
        if self.source == "silence":
            return "Silence"
        if self.announcement is not None:
            return self.announcement.name
        if self.schedule is not None:
            return self.schedule.name
        return "Nothing scheduled"


def effective_baseline_order(document: StoreDocument) -> list[str]:
    bag = [item.id for item in document.enabled_baselines()]
    if not bag:
        return []
    current = [item for item in document.rotation.order if item in bag]
    missing = [item for item in bag if item not in current]
    if not current:
        order = list(bag)
        if document.settings.baseline_randomize:
            random.Random().shuffle(order)
        return order
    return current + missing


def _baseline_can_play(document: StoreDocument, schedule: Schedule) -> bool:
    if schedule.is_silence:
        return True
    return not announcement_cannot_play(
        document, document.announcement_by_id(schedule.announcement_id)
    )


def take_baseline(
    document: StoreDocument,
    order: list[str],
    index: int,
) -> tuple[Schedule | None, list[str], int]:
    """The next playable baseline, and the rotation index after it is used.

    Unplayable baselines are skipped. When every baseline can play, this advances
    one step, including the shuffle at the end of a cycle.
    """
    if not order:
        return None, order, index
    count = len(order)
    position = index % count
    for _step in range(count):
        schedule = document.schedule_by_id(order[position])
        nxt = position + 1
        wrapped = nxt >= count
        if wrapped:
            nxt = 0
        if schedule is not None and _baseline_can_play(document, schedule):
            new_order = list(order)
            if wrapped and document.settings.baseline_randomize:
                random.Random().shuffle(new_order)
            return schedule, new_order, nxt
        position = nxt
    return None, order, index % count


def peek_baseline(document: StoreDocument) -> Schedule | None:
    order = effective_baseline_order(document)
    if not order:
        return None
    schedule, _order, _index = take_baseline(document, order, document.rotation.index)
    return schedule


def next_rotation(document: StoreDocument, slot_key: str) -> tuple[list[str], int, str]:
    """Advance the bag after a baseline owns `slot_key`. Same slot is a no-op (busy retry)."""
    if document.rotation.last_consumed_slot == slot_key:
        return (
            document.rotation.order or effective_baseline_order(document),
            document.rotation.index,
            slot_key,
        )
    order = effective_baseline_order(document)
    if not order:
        return [], 0, slot_key
    _schedule, new_order, index = take_baseline(document, order, document.rotation.index)
    return new_order, index, slot_key


def _resolved(slot: Slot, schedule: Schedule | None, document: StoreDocument) -> ResolvedSlot:
    if schedule is None:
        return ResolvedSlot(
            slot=slot,
            fire_at=slot.center,
            schedule=None,
            announcement=None,
            source="none",
            warning="No enabled baseline schedule. Add one so empty slots have something to say.",
        )
    announcement = document.announcement_by_id(schedule.announcement_id)
    offset = 0 if schedule.kind == "baseline" else schedule.offset_minutes
    fire_at = slot.fire_at(offset)
    if schedule.is_silence:
        return ResolvedSlot(
            slot=slot,
            fire_at=fire_at,
            schedule=schedule,
            announcement=None,
            source="silence",
        )
    if announcement is None:
        return ResolvedSlot(
            slot=slot,
            fire_at=fire_at,
            schedule=schedule,
            announcement=None,
            source="none",
            warning=f"{schedule.name} points at a missing announcement.",
        )
    if schedule.kind == "emergency":
        source = "emergency"
    elif schedule.kind == "baseline":
        source = "baseline"
    else:
        source = "overlay"
    return ResolvedSlot(
        slot=slot,
        fire_at=fire_at,
        schedule=schedule,
        announcement=announcement,
        source=source,
    )


def _overlay_unplayable(document: StoreDocument, schedule: Schedule) -> bool:
    if schedule.is_silence:
        return False
    announcement = document.announcement_by_id(schedule.announcement_id)
    return announcement is None or announcement_cannot_play(document, announcement)


def _skip_reason(document: StoreDocument, skipped: Schedule) -> str:
    announcement = document.announcement_by_id(skipped.announcement_id)
    if announcement is not None and newsline_blocks_slot(announcement):
        return "Newsline is not available"
    parts = too_long_parts(document, announcement) if announcement is not None else []
    if parts:
        return "; ".join(parts)
    return f"{skipped.name} cannot play"


def _substitution(document: StoreDocument, skipped: Schedule, played: ResolvedSlot) -> ResolvedSlot:
    reason = _skip_reason(document, skipped)
    if played.announcement is not None:
        instead = f"{played.announcement.name} will play instead."
    else:
        instead = "The slot will be silent. No valid baseline is available."
    detail = f"{skipped.name} will not play — {reason}. {instead}"
    return replace(played, skipped_detail=detail, warning=detail)


def _annotate(document: StoreDocument, resolved: ResolvedSlot) -> ResolvedSlot:
    if resolved.schedule is None or resolved.announcement is None:
        return resolved
    if resolved.announcement.kind != "newsline":
        return resolved
    parts = too_long_parts(document, resolved.announcement)
    if not parts:
        return resolved
    if blocks_playback(document, resolved.announcement):
        return resolved
    detail = (
        f"{resolved.schedule.name} plays, but "
        + "; ".join(parts)
        + " will be left out."
    )
    return replace(resolved, warning=detail)


def resolve_slot(document: StoreDocument, slot: Slot) -> ResolvedSlot:
    winner, _ties = pick_overlay(document.schedules, slot)
    skipped = winner if winner is not None and _overlay_unplayable(document, winner) else None
    if winner is not None and skipped is None:
        return _annotate(document, _resolved(slot, winner, document))
    played = _resolved(slot, peek_baseline(document), document)
    if skipped is None:
        return played
    return _substitution(document, skipped, played)


def resolve_window(
    document: StoreDocument,
    after: datetime,
    until: datetime,
) -> list[ResolvedSlot]:
    """Upcoming slots with a simulated baseline walk (does not persist rotation)."""
    zone = zone_for(document.settings.timezone)
    order = effective_baseline_order(document)
    index = document.rotation.index % len(order) if order else 0
    results: list[ResolvedSlot] = []
    for slot in iter_slots(after, until, zone):
        winner, _ties = pick_overlay(document.schedules, slot)
        skipped = winner if winner is not None and _overlay_unplayable(document, winner) else None
        if winner is not None and skipped is None:
            results.append(_annotate(document, _resolved(slot, winner, document)))
            continue
        if not order:
            played = _resolved(slot, None, document)
        else:
            baseline, order, index = take_baseline(document, order, index)
            played = _resolved(slot, baseline, document)
        if skipped is not None:
            played = _substitution(document, skipped, played)
        results.append(played)
    return results


def next_transmission(document: StoreDocument, after: datetime) -> ResolvedSlot | None:
    zone = zone_for(document.settings.timezone)
    until = as_local(after, zone) + timedelta(days=3)
    for item in resolve_window(document, after, until):
        if item.source in {"baseline", "overlay", "emergency"} and item.announcement is not None:
            return item
    return None


def find_conflicts(
    candidate: Schedule,
    document: StoreDocument,
    *,
    skip_id: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Same slot, same priority, two enabled overlays."""
    if not candidate.enabled or not candidate.is_overlay:
        return []
    zone = zone_for(document.settings.timezone)
    start = now or datetime.now(zone)
    until = start + timedelta(days=CONFLICT_HORIZON_DAYS)
    others = [
        item
        for item in document.schedules
        if item.enabled and item.is_overlay and item.id != (skip_id or candidate.id)
    ]
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    probe = candidate.model_copy(update={"enabled": True})
    for slot in iter_slots(start - timedelta(microseconds=1), until, zone):
        if not overlay_matches(probe, slot):
            continue
        _winner, ties = pick_overlay([probe, *others], slot)
        rivals = [item for item in ties if item.id != probe.id and item.priority == probe.priority]
        for rival in rivals:
            key = f"{rival.id}:{slot.key}"
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "name": rival.name,
                    "summary": summarize(rival),
                    "slot": slot.stamp,
                    "at": slot.center.isoformat(),
                }
            )
            break
        if hits:
            break
    return hits


def clock_for_day(document: StoreDocument, day: date) -> list[ResolvedSlot]:
    zone = zone_for(document.settings.timezone)
    start = datetime(day.year, day.month, day.day, tzinfo=zone) - timedelta(microseconds=1)
    until = datetime(day.year, day.month, day.day, tzinfo=zone) + timedelta(days=1)
    wanted = {slot.key for slot in iter_day_slots(day, zone)}
    return [item for item in resolve_window(document, start, until) if item.slot.key in wanted]


def next_runs_by_schedule(document: StoreDocument, now: datetime) -> dict[str, datetime]:
    found: dict[str, datetime] = {}
    until = now + timedelta(days=CONFLICT_HORIZON_DAYS)
    for item in resolve_window(document, now, until):
        if item.schedule is None or item.schedule.id in found:
            continue
        found[item.schedule.id] = item.fire_at
    return found


def flatten_schedule_row(
    schedule: Schedule,
    document: StoreDocument,
    now: datetime,
    next_runs: dict[str, datetime] | None = None,
) -> ScheduleRow:
    announcement = document.announcement_by_id(schedule.announcement_id)
    nxt = (next_runs or {}).get(schedule.id)
    if nxt is None and next_runs is None:
        nxt = next_runs_by_schedule(document, now).get(schedule.id)
    last = schedule.last_run_at
    return {
        "schedule_id": schedule.id,
        "name": schedule.name,
        "kind": schedule.kind,
        "enabled": schedule.enabled,
        "announcement_id": schedule.announcement_id,
        "announcement_name": announcement.name if announcement else None,
        "summary": summarize(schedule),
        "warning": schedule_warning(schedule, document, now),
        "priority": schedule.priority,
        "offset_minutes": schedule.offset_minutes,
        "last_run_at": last.isoformat() if last else None,
        "next_run_at": nxt.isoformat() if nxt else None,
    }
