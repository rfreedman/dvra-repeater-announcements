from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.config import STATIC_DIR, session_secret
from app.deps import AuthUser
from app.home_live import build_home_live_status
from app.pcm_cache import warm_all
from app.radio import active_radio_mode_label
from app.registry import get_registry
from app.routes.announcements import router as announcements_router
from app.routes.auth import router as auth_router
from app.routes.schedules import router as schedules_router
from app.routes.speak import router as speak_router
from app.scheduler import start_scheduler, stop_scheduler
from app.sse_hub import (
    format_sse,
    shutdown_sse_streams,
    sse_shutting_down,
    subscribe,
    subscribe_stats,
    unsubscribe,
    unsubscribe_stats,
)
from app.startup import StartupError, fail_startup, init_radio_at_startup
from app.startup_gate import clear_startup_ready, is_startup_ready, mark_startup_ready
from app.stats_sse import stats_broadcast_loop
from app.store import get_store
from app.system_status import read_system_stats
from app.users import get_user_store
from app.uvicorn_shutdown import register_stats_broadcast_stop

log = logging.getLogger("app.server")


def _sync_startup_for_tests() -> bool:
    import os

    return os.environ.get("TTS_SKIP_DOTENV", "").strip() == "1"


async def _complete_startup(started: float, stats_stop: asyncio.Event) -> asyncio.Task[None] | None:
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
    mark_startup_ready()
    return asyncio.create_task(stats_broadcast_loop(stats_stop))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import app.sse_hub as sse_hub

    sse_hub.set_event_loop(asyncio.get_running_loop())
    sse_hub.clear_shutdown_state()
    clear_startup_ready()
    stats_stop = asyncio.Event()
    register_stats_broadcast_stop(stats_stop)
    stats_task: asyncio.Task[None] | None = None
    startup_bg: asyncio.Task[asyncio.Task[None] | None] | None = None

    started = time.perf_counter()
    log.info("Starting up")
    if _sync_startup_for_tests():
        stats_task = await _complete_startup(started, stats_stop)
    else:
        startup_bg = asyncio.create_task(_complete_startup(started, stats_stop))

    try:
        yield
    finally:
        stats_stop.set()
        shutdown_sse_streams()
        if stats_task is not None:
            await stats_task
        elif startup_bg is not None:
            if startup_bg.done():
                if startup_bg.exception() is None:
                    nested = startup_bg.result()
                    if nested is not None:
                        await nested
            else:
                startup_bg.cancel()
                try:
                    await startup_bg
                except asyncio.CancelledError:
                    pass
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


@app.middleware("http")
async def startup_gate_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/api/ready" or not path.startswith("/api/"):
        return await call_next(request)
    if not is_startup_ready():
        return JSONResponse(
            content={"detail": "starting"},
            status_code=503,
            headers={"Retry-After": "2"},
        )
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/system/stats")
def system_stats(_user: AuthUser) -> dict[str, object]:
    return read_system_stats()


@app.get("/api/ready")
def startup_ready() -> dict[str, bool]:
    return {"ready": is_startup_ready()}


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


app.include_router(auth_router)
app.include_router(speak_router)
app.include_router(announcements_router)
app.include_router(schedules_router)


@app.get("/")
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{APP_VERSION}}", __version__))


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
