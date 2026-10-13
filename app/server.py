from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.middleware.sessions import SessionMiddleware

from app import __version__, config
from app.audio import PcmChunk
from app.auth import (
    authenticate,
    clear_session,
    current_user_optional,
    hash_password,
    login_limiter,
    require_admin,
    require_user,
    set_session_user,
    validate_password,
)
from app.config import (
    DEFAULT_BUSY_GIVE_UP_SECONDS,
    DEFAULT_BUSY_RETRY_SECONDS,
    DEFAULT_SENTENCE_PAUSE,
    DEFAULT_SPEED,
    MAX_BUSY_GIVE_UP_SECONDS,
    MAX_BUSY_RETRY_SECONDS,
    MAX_SENTENCE_PAUSE,
    MAX_SPEECH_SPEED,
    MAX_TEXT_CHARS,
    MIN_SPEECH_SPEED,
    STATIC_DIR,
    TIMEZONE,
    session_secret,
)
from app.engines.base import VoiceError
from app.models import (
    Announcement,
    Schedule,
    ScheduleKind,
    Settings,
    StoreDocument,
    normalize_announcement_text,
)
from app.registry import get_registry
from app.schedule_logic import (
    clock_for_day,
    document_warnings,
    find_conflicts,
    flatten_schedule_row,
    next_runs_by_schedule,
    offset_allowed,
)
from app.pcm_cache import (
    get_pcm_cache,
    warm_all,
    warm_announcement,
    warm_announcement_in_background,
)
from app.home_live import build_home_live_status, notify_home_data_changed, notify_home_live_status
from app.scheduler import (
    clear_manual_trigger_drop_notice,
    peek_manual_trigger_drop_notice,
    run_manual_trigger,
    start_scheduler,
    stop_scheduler,
    sync_jobs,
)
from app.sse_hub import (
    format_sse,
    shutdown_sse_streams,
    sse_shutting_down,
    subscribe,
    subscribe_stats,
    unsubscribe,
    unsubscribe_stats,
)
from app.stats_sse import stats_broadcast_loop
from app.uvicorn_shutdown import register_stats_broadcast_stop
from app.radio import active_radio_mode_label
from app.startup import StartupError, fail_startup, init_radio_at_startup
from app.store import get_store
from app.system_status import read_system_stats
from app.users import Role, User, get_user_store

log = logging.getLogger("app.server")
_END = object()

AdminUser = Annotated[User, Depends(require_admin)]
AuthUser = Annotated[User, Depends(require_user)]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    started = time.perf_counter()
    log.info("Starting up")
    try:
        get_store().document()
        get_user_store().count()
        await asyncio.to_thread(init_radio_at_startup)
        await asyncio.to_thread(get_registry().preload)
        await asyncio.to_thread(warm_all)
        start_scheduler()
    except StartupError as exc:
        fail_startup(str(exc))
    except Exception as exc:
        fail_startup(str(exc) or exc.__class__.__name__)
    log.info(
        "Startup complete in %.1fs (radio mode: %s)",
        time.perf_counter() - started,
        active_radio_mode_label(),
    )
    import app.sse_hub as sse_hub

    sse_hub.set_event_loop(asyncio.get_running_loop())
    sse_hub.clear_shutdown_state()
    stats_stop = asyncio.Event()
    register_stats_broadcast_stop(stats_stop)
    stats_task = asyncio.create_task(stats_broadcast_loop(stats_stop))
    try:
        yield
    finally:
        stats_stop.set()
        shutdown_sse_streams()
        await stats_task
    stop_scheduler()


app = FastAPI(title="DVRA Repeater Announcements", version=__version__, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret(),
    session_cookie="announcements_session",
    max_age=None,
    same_site="lax",
    https_only=False,
)
registry = get_registry()


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str | None = None
    speed: float = Field(default=DEFAULT_SPEED, gt=MIN_SPEECH_SPEED, le=MAX_SPEECH_SPEED)
    sentence_pause: float = Field(
        default=DEFAULT_SENTENCE_PAUSE, ge=0.0, le=MAX_SENTENCE_PAUSE
    )

    @field_validator("text")
    @classmethod
    def text_present(cls, value: str) -> str:
        return normalize_announcement_text(value)


class PrepareRequest(BaseModel):
    voice: str | None = None


class ScheduleIn(BaseModel):
    id: str | None = None
    name: str
    enabled: bool = True
    kind: ScheduleKind
    timezone: str = TIMEZONE
    announcement_id: str | None = None
    offset_minutes: int = 0
    slots: list[str] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    occurrence: int | None = None
    skip_months: list[int] = Field(default_factory=list)
    on_date: date | None = None
    start_date: date | None = None
    event_date: date | None = None
    days_before: int | None = None
    event_slot: str | None = None
    priority: int | None = None


class SchedulePatch(BaseModel):
    enabled: bool | None = None


class AnnouncementIn(BaseModel):
    name: str
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str | None = None
    speed: float = Field(default=DEFAULT_SPEED, gt=MIN_SPEECH_SPEED, le=MAX_SPEECH_SPEED)
    sentence_pause: float = Field(
        default=DEFAULT_SENTENCE_PAUSE, ge=0.0, le=MAX_SENTENCE_PAUSE
    )
    busy_retry_seconds: float = Field(
        default=DEFAULT_BUSY_RETRY_SECONDS, gt=0, le=MAX_BUSY_RETRY_SECONDS
    )
    busy_give_up_seconds: float = Field(
        default=DEFAULT_BUSY_GIVE_UP_SECONDS, gt=0, le=MAX_BUSY_GIVE_UP_SECONDS
    )

    @field_validator("text")
    @classmethod
    def text_present(cls, value: str) -> str:
        return normalize_announcement_text(value)


class SettingsIn(BaseModel):
    slot_half_window_minutes: int | None = None
    baseline_randomize: bool | None = None


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class SetupIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    role: Role = "readonly"


class UserUpdateIn(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=1, max_length=256)
    role: Role | None = None


def _warm_in_background(item: Announcement) -> None:
    warm_announcement_in_background(item, warm_announcement)


def _build_schedule(payload: ScheduleIn) -> Schedule:
    data = payload.model_dump(exclude_none=True)
    if not data.get("id"):
        data.pop("id", None)
    try:
        return Schedule.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_validation_detail(exc)) from exc


def _validation_detail(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", ()) if item != "body")
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "Invalid schedule"


def _reject_schedule(schedule: Schedule, *, skip_id: str | None = None) -> None:
    store = get_store()
    document = store.document()
    if schedule.announcement_id and document.announcement_by_id(schedule.announcement_id) is None:
        raise HTTPException(status_code=400, detail="Announcement not found")
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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/system/stats")
def system_stats(_user: AuthUser) -> dict[str, object]:
    return read_system_stats()


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, object]:
    store = get_user_store()
    if store.count() == 0:
        return {"authenticated": False, "needs_setup": True, "user": None}
    user = current_user_optional(request)
    if user is None:
        return {"authenticated": False, "needs_setup": False, "user": None}
    return {"authenticated": True, "needs_setup": False, "user": user.public()}


@app.post("/api/auth/setup")
def auth_setup(req: SetupIn, request: Request) -> dict[str, object]:
    store = get_user_store()
    if store.count() != 0:
        raise HTTPException(status_code=409, detail="Setup already completed")
    try:
        password = validate_password(req.password)
        user = store.create(
            username=req.username,
            password_hash=hash_password(password),
            role="admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    set_session_user(request, user)
    return {"user": user.public()}


@app.post("/api/auth/login")
def auth_login(req: LoginIn, request: Request) -> dict[str, object]:
    if get_user_store().count() == 0:
        raise HTTPException(status_code=409, detail="Setup required")
    client = request.client.host if request.client else "unknown"
    limiter_key = f"{client}:{req.username.strip().lower()}"
    retry_after = login_limiter.retry_after(limiter_key)
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed sign-ins. Try again in {int(retry_after) + 1} seconds.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    user = authenticate(req.username, req.password)
    if user is None:
        login_limiter.record_failure(limiter_key)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    login_limiter.reset(limiter_key)
    set_session_user(request, user)
    return {"user": user.public()}


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> dict[str, bool]:
    clear_session(request)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user: AuthUser) -> dict[str, object]:
    return {"user": user.public()}


@app.get("/api/users")
def list_users(_admin: AdminUser) -> dict[str, object]:
    users = sorted(get_user_store().list_users(), key=lambda item: item.username.lower())
    return {"users": [item.public() for item in users]}


@app.post("/api/users")
def create_user(req: UserCreateIn, _admin: AdminUser) -> dict[str, object]:
    try:
        password = validate_password(req.password)
        user = get_user_store().create(
            username=req.username,
            password_hash=hash_password(password),
            role=req.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user.public()


@app.put("/api/users/{user_id}")
def update_user(user_id: str, req: UserUpdateIn, _admin: AdminUser) -> dict[str, object]:
    store = get_user_store()
    if store.get(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        password_hash = None
        if req.password is not None:
            password_hash = hash_password(validate_password(req.password))
        user = store.update(
            user_id,
            username=req.username,
            password_hash=password_hash,
            role=req.role,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user.public()


@app.delete("/api/users/{user_id}")
def delete_user(user_id: str, _admin: AdminUser) -> dict[str, bool]:
    try:
        if not get_user_store().delete(user_id):
            raise HTTPException(status_code=404, detail="User not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/voices")
def voices(_user: AuthUser) -> dict[str, object]:
    return registry.voices_payload()


@app.post("/api/prepare")
def prepare(req: PrepareRequest, _user: AuthUser) -> dict[str, str]:
    try:
        voice_id = registry.prepare(req.voice)
    except VoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"voice": voice_id}


@app.post("/api/speak")
async def speak(req: SpeakRequest, request: Request, _user: AuthUser) -> StreamingResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    try:
        voice_id, sample_rate, chunks = await asyncio.to_thread(
            lambda: registry.stream_synthesize(
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


@app.get("/api/settings")
def get_settings(_user: AuthUser) -> dict[str, object]:
    return get_store().document().settings.model_dump(mode="json")


@app.put("/api/settings")
def put_settings(req: SettingsIn, _admin: AdminUser) -> dict[str, object]:
    store = get_store()
    current = store.document().settings
    data = current.model_dump()
    if req.slot_half_window_minutes is not None:
        data["slot_half_window_minutes"] = req.slot_half_window_minutes
    if req.baseline_randomize is not None:
        data["baseline_randomize"] = req.baseline_randomize
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
    sync_jobs()
    notify_home_data_changed()
    return stored.model_dump(mode="json")


@app.get("/api/schedules")
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
    }


async def _home_live_event_stream(request: Request) -> AsyncIterator[str]:
    queue = subscribe()
    try:
        yield format_sse("snapshot", build_home_live_status())
        while True:
            if sse_shutting_down() or await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                if sse_shutting_down():
                    break
                yield ": ping\n\n"
                continue
            if payload is None:
                break
            yield format_sse(payload.event, payload.data)
    finally:
        unsubscribe(queue)


@app.get("/api/events/home")
async def home_live_events(request: Request, _user: AuthUser) -> StreamingResponse:
    return StreamingResponse(
        _home_live_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _system_stats_event_stream(request: Request) -> AsyncIterator[str]:
    queue = subscribe_stats()
    try:
        yield format_sse("snapshot", await asyncio.to_thread(read_system_stats))
        while True:
            if sse_shutting_down() or await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                if sse_shutting_down():
                    break
                yield ": ping\n\n"
                continue
            if payload is None:
                break
            yield format_sse(payload.event, payload.data)
    finally:
        unsubscribe_stats(queue)


@app.get("/api/events/stats")
async def system_stats_events(request: Request, _user: AuthUser) -> StreamingResponse:
    return StreamingResponse(
        _system_stats_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/manual-trigger-drop/dismiss")
def dismiss_manual_trigger_drop(_admin: AdminUser) -> dict[str, bool]:
    clear_manual_trigger_drop_notice()
    notify_home_live_status(force=True)
    return {"ok": True}


@app.get("/api/announcements")
def list_announcements(_user: AuthUser) -> dict[str, object]:
    return {"announcements": [item.model_dump(mode="json") for item in get_store().list_announcements()]}


@app.get("/api/announcements/{announcement_id}")
def get_announcement(announcement_id: str, _user: AuthUser) -> dict[str, object]:
    item = get_store().get(announcement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return item.model_dump(mode="json")


@app.post("/api/announcements")
def create_announcement(req: AnnouncementIn, _admin: AdminUser) -> dict[str, object]:
    item = Announcement(
        name=req.name,
        text=req.text.strip(),
        voice=req.voice,
        speed=req.speed,
        sentence_pause=req.sentence_pause,
        busy_retry_seconds=req.busy_retry_seconds,
        busy_give_up_seconds=req.busy_give_up_seconds,
    )
    stored = get_store().save_announcement(item)
    _warm_in_background(stored)
    notify_home_data_changed()
    return stored.model_dump(mode="json")


@app.put("/api/announcements/{announcement_id}")
def update_announcement(
    announcement_id: str, req: AnnouncementIn, _admin: AdminUser
) -> dict[str, object]:
    store = get_store()
    existing = store.get(announcement_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    updated = existing.model_copy(
        update={
            "name": req.name,
            "text": req.text.strip(),
            "voice": req.voice,
            "speed": req.speed,
            "sentence_pause": req.sentence_pause,
            "busy_retry_seconds": req.busy_retry_seconds,
            "busy_give_up_seconds": req.busy_give_up_seconds,
        }
    )
    stored = store.save_announcement(updated)
    _warm_in_background(stored)
    sync_jobs()
    notify_home_data_changed()
    return stored.model_dump(mode="json")


@app.delete("/api/announcements/{announcement_id}")
def delete_announcement(announcement_id: str, _admin: AdminUser) -> dict[str, bool]:
    try:
        if not get_store().delete_announcement(announcement_id):
            raise HTTPException(status_code=404, detail="Announcement not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    get_pcm_cache().drop(announcement_id)
    sync_jobs()
    notify_home_data_changed()
    return {"ok": True}


@app.post("/api/schedules")
def create_schedule(req: ScheduleIn, _admin: AdminUser) -> dict[str, object]:
    incoming = _build_schedule(req)
    _reject_schedule(incoming)
    stored = get_store().save_schedule(incoming)
    sync_jobs()
    notify_home_data_changed()
    return stored.model_dump(mode="json")


@app.get("/api/schedules/{schedule_id}")
def get_schedule(schedule_id: str, _user: AuthUser) -> dict[str, object]:
    item = get_store().get_schedule(schedule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return item.model_dump(mode="json")


@app.put("/api/schedules/{schedule_id}")
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


@app.patch("/api/schedules/{schedule_id}")
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


@app.post("/api/schedules/{schedule_id}/trigger")
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


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, _admin: AdminUser) -> dict[str, bool]:
    if not get_store().delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    sync_jobs()
    notify_home_data_changed()
    return {"ok": True}


@app.get("/")
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{APP_VERSION}}", __version__))


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
