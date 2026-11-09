"""Public schedule API.

Kind matching, playability, summaries, warnings, and slot resolution live in
``schedule_match``, ``schedule_play``, ``schedule_summary``, ``schedule_warnings``,
and ``schedule_resolve``. Import from here so routes and the scheduler stay stable.
"""

from __future__ import annotations

from app.schedule_match import (
    matching_overlays as matching_overlays,
    nth_weekday_date as nth_weekday_date,
    overlay_matches as overlay_matches,
    pick_overlay as pick_overlay,
)
from app.schedule_play import announcement_cannot_play as announcement_cannot_play
from app.schedule_resolve import (
    CONFLICT_HORIZON_DAYS as CONFLICT_HORIZON_DAYS,
    ResolvedSlot as ResolvedSlot,
    clock_for_day as clock_for_day,
    effective_baseline_order as effective_baseline_order,
    find_conflicts as find_conflicts,
    flatten_schedule_row as flatten_schedule_row,
    next_rotation as next_rotation,
    next_runs_by_schedule as next_runs_by_schedule,
    next_transmission as next_transmission,
    peek_baseline as peek_baseline,
    resolve_slot as resolve_slot,
    resolve_window as resolve_window,
    take_baseline as take_baseline,
)
from app.schedule_summary import summarize as summarize
from app.schedule_warnings import (
    document_warnings as document_warnings,
    offset_allowed as offset_allowed,
    schedule_warning as schedule_warning,
)
