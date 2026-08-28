from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.audio import PcmChunk
from app.config import (
    DEFAULT_BUSY_GIVE_UP_SECONDS,
    DEFAULT_BUSY_RETRY_SECONDS,
    DEFAULT_SENTENCE_PAUSE,
    DEFAULT_SPEED,
    MAX_TEXT_CHARS,
    STATIC_DIR,
    TIMEZONE,
)
from app.engines.base import VoiceError
from app.models import Announcement, Schedule
from app.registry import get_registry
from app.schedule_logic import find_conflicts, next_run_at, summarize, trigger_warning
from app.scheduler import get_running, start_scheduler, stop_scheduler, sync_jobs
from app.store import get_store

log = logging.getLogger("app.server")
_END = object()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="TTS Stream", version="1.0.0", lifespan=lifespan)
registry = get_registry()


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str | None = None
    speed: float = Field(default=DEFAULT_SPEED, gt=0.25, le=2.0)
    sentence_pause: float = Field(default=DEFAULT_SENTENCE_PAUSE, ge=0.0, le=2.0)


class PrepareRequest(BaseModel):
    voice: str | None = None


class ExclusionIn(BaseModel):
    kind: Literal["day", "day_time"] | None = None
    days: list[str]
    start: str | None = None
    end: str | None = None
    time: str | None = None
    note: str | None = None


class ScheduleIn(BaseModel):
    id: str | None = None
    enabled: bool = True
    kind: Literal["hourly", "daily", "weekly", "once"]
    timezone: str = TIMEZONE
    minute: int | None = None
    minutes: list[int] = Field(default_factory=list)
    times: list[str] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    at: datetime | None = None
    exclusions: list[ExclusionIn] = Field(default_factory=list)


class SchedulePatch(BaseModel):
    enabled: bool | None = None


class AnnouncementIn(BaseModel):
    name: str
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str | None = None
    speed: float = Field(default=DEFAULT_SPEED, gt=0.25, le=2.0)
    sentence_pause: float = Field(default=DEFAULT_SENTENCE_PAUSE, ge=0.0, le=2.0)
    busy_retry_seconds: float = Field(default=DEFAULT_BUSY_RETRY_SECONDS, gt=0, le=120)
    busy_give_up_seconds: float = Field(default=DEFAULT_BUSY_GIVE_UP_SECONDS, gt=0, le=600)
    schedule: ScheduleIn | None = None


def _build_schedule(payload: ScheduleIn) -> Schedule:
    data = payload.model_dump(exclude_none=True)
    if not data.get("id"):
        data.pop("id", None)
    try:
        return Schedule.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _reject_conflicts(schedule: Schedule, *, skip_id: str | None = None) -> None:
    others: list[tuple[str, Schedule]] = []
    for announcement in get_store().list_announcements():
        for row in announcement.schedules:
            if skip_id and row.id == skip_id:
                continue
            others.append((announcement.name, row))
    hits = find_conflicts(schedule, others)
    if not hits:
        return
    labels = [f"{hit['name']} ({hit['summary']})" for hit in hits]
    raise HTTPException(
        status_code=409,
        detail="Conflicts with " + "; ".join(labels),
    )


def _flatten_row(announcement, schedule) -> dict[str, object]:
    now = datetime.now(ZoneInfo(schedule.timezone if schedule else TIMEZONE))
    nxt = next_run_at(schedule, now) if schedule else None
    last = schedule.last_run_at if schedule else None
    return {
        "announcement_id": announcement.id,
        "announcement_name": announcement.name,
        "schedule_id": schedule.id if schedule else None,
        "enabled": bool(schedule and schedule.enabled),
        "summary": summarize(schedule) if schedule else "No schedule",
        "warning": trigger_warning(announcement, schedule, now),
        "last_run_at": last.isoformat() if last else None,
        "next_run_at": nxt.isoformat() if nxt else None,
    }


def _flatten_schedules() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for announcement in get_store().list_announcements():
        if not announcement.schedules:
            rows.append(_flatten_row(announcement, None))
            continue
        for schedule in announcement.schedules:
            rows.append(_flatten_row(announcement, schedule))
    rows.sort(
        key=lambda row: (
            0 if row.get("warning") else 1,
            str(row["next_run_at"] or "z"),
            str(row["announcement_name"] or ""),
        )
    )
    return rows


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/voices")
def voices() -> dict[str, object]:
    return registry.voices_payload()


@app.post("/api/prepare")
def prepare(req: PrepareRequest) -> dict[str, str]:
    try:
        voice_id = registry.prepare(req.voice)
    except VoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"voice": voice_id}


@app.post("/api/speak")
async def speak(req: SpeakRequest, request: Request) -> StreamingResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    try:
        voice_id, sample_rate, chunks = await asyncio.to_thread(
            lambda: registry.synthesize(
                text,
                voice=req.voice,
                speed=req.speed,
                sentence_pause=req.sentence_pause,
            )
        )
    except VoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return StreamingResponse(
        _pcm_stream(request, chunks),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Voice": voice_id,
            "X-Sample-Rate": str(sample_rate),
            "X-Sample-Width": "2",
            "X-Channels": "1",
        },
    )


async def _pcm_stream(request: Request, chunks: Iterator[PcmChunk]) -> AsyncIterator[bytes]:
    iterator = iter(chunks)
    gen_lock = threading.Lock()

    def take_next() -> PcmChunk | object:
        with gen_lock:
            return next(iterator, _END)

    def close_gen() -> None:
        with gen_lock:
            close = getattr(iterator, "close", None)
            if not callable(close):
                return
            try:
                close()
            except Exception:
                pass

    try:
        while True:
            if await request.is_disconnected():
                break
            chunk = await asyncio.to_thread(take_next)
            if chunk is _END:
                break
            if await request.is_disconnected():
                break
            if chunk.pcm_int16:
                yield chunk.pcm_int16
    finally:
        await asyncio.to_thread(close_gen)


def _upcoming(rows: list[dict[str, object]], running: dict[str, object] | None) -> dict[str, object] | None:
    if running:
        return {
            "announcement_id": running.get("announcement_id"),
            "announcement_name": running.get("announcement_name"),
            "schedule_id": running.get("schedule_id"),
            "at": running.get("started_at"),
            "status": "running",
        }
    soonest: dict[str, object] | None = None
    soonest_dt: datetime | None = None
    for row in rows:
        iso = row.get("next_run_at")
        if not iso:
            continue
        when = datetime.fromisoformat(str(iso))
        if soonest_dt is None or when < soonest_dt:
            soonest = row
            soonest_dt = when
    if soonest is None:
        return None
    return {
        "announcement_id": soonest.get("announcement_id"),
        "announcement_name": soonest.get("announcement_name"),
        "schedule_id": soonest.get("schedule_id"),
        "at": soonest.get("next_run_at"),
        "status": "waiting",
    }


@app.get("/api/schedules")
def list_schedules() -> dict[str, object]:
    rows = _flatten_schedules()
    return {
        "now": datetime.now(ZoneInfo(TIMEZONE)).isoformat(),
        "upcoming": _upcoming(rows, get_running()),
        "schedules": rows,
    }


@app.get("/api/announcements")
def list_announcements() -> dict[str, object]:
    return {"announcements": [item.model_dump(mode="json") for item in get_store().list_announcements()]}


@app.get("/api/announcements/{announcement_id}")
def get_announcement(announcement_id: str) -> dict[str, object]:
    item = get_store().get(announcement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return item.model_dump(mode="json")


@app.post("/api/announcements")
def create_announcement(req: AnnouncementIn) -> dict[str, object]:
    store = get_store()
    item = Announcement(
        name=req.name,
        text=req.text.strip(),
        voice=req.voice,
        speed=req.speed,
        sentence_pause=req.sentence_pause,
        busy_retry_seconds=req.busy_retry_seconds,
        busy_give_up_seconds=req.busy_give_up_seconds,
        schedules=[],
    )
    if req.schedule is not None:
        incoming = _build_schedule(req.schedule)
        _reject_conflicts(incoming)
        item.schedules.append(incoming)
    stored = store.save(item)
    sync_jobs()
    return stored.model_dump(mode="json")


@app.put("/api/announcements/{announcement_id}")
def update_announcement(announcement_id: str, req: AnnouncementIn) -> dict[str, object]:
    store = get_store()
    existing = store.get(announcement_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    schedules = list(existing.schedules)
    if req.schedule is not None:
        incoming = _build_schedule(req.schedule)
        _reject_conflicts(incoming, skip_id=incoming.id)
        replaced = False
        for index, row in enumerate(schedules):
            if row.id == incoming.id:
                incoming = incoming.model_copy(update={"last_run_at": row.last_run_at})
                schedules[index] = incoming
                replaced = True
                break
        if not replaced:
            schedules.append(incoming)
    updated = existing.model_copy(
        update={
            "name": req.name,
            "text": req.text.strip(),
            "voice": req.voice,
            "speed": req.speed,
            "sentence_pause": req.sentence_pause,
            "busy_retry_seconds": req.busy_retry_seconds,
            "busy_give_up_seconds": req.busy_give_up_seconds,
            "schedules": schedules,
        }
    )
    stored = store.save(updated)
    sync_jobs()
    return stored.model_dump(mode="json")


@app.delete("/api/announcements/{announcement_id}")
def delete_announcement(announcement_id: str) -> dict[str, bool]:
    if not get_store().delete_announcement(announcement_id):
        raise HTTPException(status_code=404, detail="Announcement not found")
    sync_jobs()
    return {"ok": True}


@app.post("/api/announcements/{announcement_id}/schedules")
def add_schedule(announcement_id: str, req: ScheduleIn) -> dict[str, object]:
    incoming = _build_schedule(req)
    _reject_conflicts(incoming, skip_id=incoming.id)
    stored = get_store().upsert_schedule(announcement_id, incoming)
    if stored is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    sync_jobs()
    return stored.model_dump(mode="json")


@app.put("/api/announcements/{announcement_id}/schedules/{schedule_id}")
def replace_schedule(announcement_id: str, schedule_id: str, req: ScheduleIn) -> dict[str, object]:
    payload = req.model_copy(update={"id": schedule_id})
    incoming = _build_schedule(payload)
    _reject_conflicts(incoming, skip_id=schedule_id)
    stored = get_store().upsert_schedule(announcement_id, incoming)
    if stored is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    sync_jobs()
    return stored.model_dump(mode="json")


@app.patch("/api/announcements/{announcement_id}/schedules/{schedule_id}")
def patch_schedule(announcement_id: str, schedule_id: str, req: SchedulePatch) -> dict[str, object]:
    store = get_store()
    item = store.get(announcement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    schedule = item.schedule_by_id(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if req.enabled is not None:
        schedule = schedule.model_copy(update={"enabled": req.enabled})
    if schedule.enabled:
        _reject_conflicts(schedule, skip_id=schedule_id)
    stored = store.upsert_schedule(announcement_id, schedule)
    if stored is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    sync_jobs()
    return stored.model_dump(mode="json")


@app.delete("/api/announcements/{announcement_id}/schedules/{schedule_id}")
def delete_schedule(announcement_id: str, schedule_id: str) -> dict[str, bool]:
    if get_store().delete_schedule(announcement_id, schedule_id) is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    sync_jobs()
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
