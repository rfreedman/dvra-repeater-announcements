"""Schedule, settings, and manual-trigger routes."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app import config
from app.deps import AdminUser, AuthUser
from app.home_live import build_home_live_status, notify_home_data_changed, notify_home_live_status
from app.models import Schedule, Settings, StoreDocument, schedule_meta
from app.schedule_logic import (
    clock_for_day,
    document_warnings,
    find_conflicts,
    flatten_schedule_row,
    next_runs_by_schedule,
    offset_allowed,
)
from app.scheduler import (
    clear_manual_trigger_drop_notice,
    peek_manual_trigger_drop_notice,
    run_manual_trigger,
    sync_jobs,
)
from app.schemas import ScheduleIn, SchedulePatch, SettingsIn
from app.store import get_store

router = APIRouter()


def _newsline_status() -> dict[str, object]:
    from app.newsline.poll import status_payload

    return status_payload()


def _recompute_lengths(store, timeout: int) -> None:
    from app.spoken_length import recompute_too_long

    for item in store.document().announcements:
        updated = recompute_too_long(item, timeout)
        if updated.too_long != item.too_long:
            store.save_announcement(updated)


def _validation_detail(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", ()) if item != "body")
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "Invalid schedule"


def _build_schedule(payload: ScheduleIn) -> Schedule:
    data = payload.model_dump(exclude_none=True)
    if not data.get("id"):
        data.pop("id", None)
    try:
        return Schedule.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_validation_detail(exc)) from exc


def _reject_schedule(schedule: Schedule, *, skip_id: str | None = None) -> None:
    store = get_store()
    document = store.document()
    if schedule.announcement_id and document.announcement_by_id(schedule.announcement_id) is None:
        raise HTTPException(status_code=400, detail="Announcement not found")
    announcement = document.announcement_by_id(schedule.announcement_id)
    if announcement is not None:
        from app.spoken_length import blocks_playback, too_long_parts

        if blocks_playback(document, announcement):
            previous = document.schedule_by_id(skip_id) if skip_id else None
            if previous is None or previous.announcement_id != schedule.announcement_id:
                parts = too_long_parts(document, announcement)
                detail = "; ".join(parts) or f"{announcement.name} cannot be scheduled"
                raise HTTPException(status_code=400, detail=detail)
    if not offset_allowed(schedule.offset_minutes, document.settings.slot_half_window_minutes):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Offset must be within ±{document.settings.slot_half_window_minutes} "
                "minutes of the slot"
            ),
        )
    if schedule.kind == "baseline":
        for other in document.schedules:
            if other.kind != "baseline" or other.id == (skip_id or schedule.id):
                continue
            if other.announcement_id == schedule.announcement_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"That announcement is already in the baseline pool ({other.name})",
                )
    hits = find_conflicts(schedule, document, skip_id=skip_id or schedule.id)
    if not hits:
        return
    hit = hits[0]
    raise HTTPException(
        status_code=409,
        detail=f"Conflicts with {hit['name']} on the {hit['slot']} slot ({hit['at']})",
    )


def _clock_payload(document: StoreDocument, day: date) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in clock_for_day(document, day):
        rows.append(
            {
                "slot": item.slot.stamp,
                "slot_at": item.slot.center.isoformat(),
                "fire_at": item.fire_at.isoformat(),
                "source": item.source,
                "schedule_id": item.schedule.id if item.schedule else None,
                "schedule_name": item.schedule.name if item.schedule else None,
                "announcement_id": item.announcement.id if item.announcement else None,
                "announcement_name": item.announcement.name if item.announcement else None,
                "label": item.label,
                "warning": item.warning,
            }
        )
    return rows


@router.get("/api/meta")
def meta(_user: AuthUser) -> dict[str, object]:
    return schedule_meta()


@router.get("/api/settings")
def get_settings(_user: AuthUser) -> dict[str, object]:
    return get_store().document().settings.model_dump(mode="json")


@router.put("/api/settings")
def put_settings(req: SettingsIn, _admin: AdminUser) -> dict[str, object]:
    store = get_store()
    current = store.document().settings
    data = current.model_dump()
    if req.slot_half_window_minutes is not None:
        data["slot_half_window_minutes"] = req.slot_half_window_minutes
    if req.baseline_randomize is not None:
        data["baseline_randomize"] = req.baseline_randomize
    if req.news_check_start is not None:
        data["news_check_start"] = req.news_check_start
    if req.news_check_stop is not None:
        data["news_check_stop"] = req.news_check_stop
    if req.repeater_timeout_seconds is not None:
        data["repeater_timeout_seconds"] = req.repeater_timeout_seconds
    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_validation_detail(exc)) from exc
    document = store.document()
    for schedule in document.schedules:
        if not offset_allowed(schedule.offset_minutes, settings.slot_half_window_minutes):
            raise HTTPException(
                status_code=400,
                detail=f"{schedule.name} offset is outside the new slot window",
            )
    stored = store.save_settings(settings)
    _recompute_lengths(store, stored.repeater_timeout_seconds)
    from app.newsline.poll import arm_next_poll

    arm_next_poll(immediate=False)
    sync_jobs()
    notify_home_data_changed()
    return stored.model_dump(mode="json")


@router.get("/api/schedules")
def list_schedules(_user: AuthUser, clock_date: date | None = None) -> dict[str, object]:
    document = get_store().document()
    now = datetime.now(ZoneInfo(document.settings.timezone))
    day = clock_date or now.date()
    next_runs = next_runs_by_schedule(document, now)
    rows = [flatten_schedule_row(item, document, now, next_runs) for item in document.schedules]
    rows.sort(
        key=lambda row: (
            0 if row["kind"] == "baseline" else 1,
            0 if row.get("warning") else 1,
            str(row["name"] or ""),
        )
    )
    live = build_home_live_status()
    return {
        **live,
        "warnings": document_warnings(document),
        "settings": document.settings.model_dump(mode="json"),
        "clock_date": day.isoformat(),
        "clock": _clock_payload(document, day),
        "schedules": rows,
        "trigger_now_enabled": config.TRIGGER_NOW,
        "newsline": _newsline_status(),
    }


@router.post("/api/manual-trigger-drop/dismiss")
def dismiss_manual_trigger_drop(_admin: AdminUser) -> dict[str, bool]:
    clear_manual_trigger_drop_notice()
    notify_home_live_status(force=True)
    return {"ok": True}


@router.post("/api/schedules")
def create_schedule(req: ScheduleIn, _admin: AdminUser) -> dict[str, object]:
    incoming = _build_schedule(req)
    _reject_schedule(incoming)
    stored = get_store().save_schedule(incoming)
    sync_jobs()
    notify_home_data_changed()
    return stored.model_dump(mode="json")


@router.get("/api/schedules/{schedule_id}")
def get_schedule(schedule_id: str, _user: AuthUser) -> dict[str, object]:
    store = get_store()
    item = store.get_schedule(schedule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    document = store.document()
    payload = item.model_dump(mode="json")
    from app.schedule_logic import schedule_warning

    payload["warning"] = schedule_warning(item, document)
    return payload


@router.put("/api/schedules/{schedule_id}")
def replace_schedule(schedule_id: str, req: ScheduleIn, _admin: AdminUser) -> dict[str, object]:
    payload = req.model_copy(update={"id": schedule_id})
    incoming = _build_schedule(payload)
    if get_store().get_schedule(schedule_id) is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    _reject_schedule(incoming, skip_id=schedule_id)
    stored = get_store().save_schedule(incoming)
    sync_jobs()
    notify_home_data_changed()
    return stored.model_dump(mode="json")


@router.patch("/api/schedules/{schedule_id}")
def patch_schedule(schedule_id: str, req: SchedulePatch, _admin: AdminUser) -> dict[str, object]:
    store = get_store()
    schedule = store.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if req.enabled is not None:
        schedule = schedule.model_copy(update={"enabled": req.enabled})
    if schedule.enabled:
        _reject_schedule(schedule, skip_id=schedule_id)
    stored = store.set_enabled(schedule_id, schedule.enabled)
    if stored is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    sync_jobs()
    notify_home_data_changed()
    return stored.model_dump(mode="json")


@router.post("/api/schedules/{schedule_id}/trigger")
def trigger_schedule_now(schedule_id: str, _admin: AdminUser) -> dict[str, str]:
    if not config.TRIGGER_NOW:
        raise HTTPException(status_code=404, detail="Not found")
    if get_store().get_schedule(schedule_id) is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    result = run_manual_trigger(schedule_id)
    if result == "missing":
        raise HTTPException(status_code=404, detail="Schedule not found")
    if result == "disabled":
        raise HTTPException(status_code=400, detail="Schedule is disabled")
    if result == "skipped_lock":
        raise HTTPException(status_code=409, detail="Another announcement is playing")
    if result == "dropped":
        notice = peek_manual_trigger_drop_notice()
        detail = (
            str(notice.get("detail"))
            if notice and notice.get("detail")
            else "Channel still busy"
        )
        raise HTTPException(status_code=409, detail=detail)
    if result == "deferred":
        notify_home_live_status(force=True)
        return {"result": "deferred"}
    notify_home_live_status(force=True)
    return {"result": result}


@router.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, _admin: AdminUser) -> dict[str, bool]:
    if not get_store().delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    sync_jobs()
    notify_home_data_changed()
    return {"ok": True}
