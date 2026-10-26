"""Manual Newsline check and browser preview."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.deps import AdminUser, AuthUser
from app.home_live import notify_home_data_changed, notify_home_live_status
from app.newsline.notices import clear_skip_notice
from app.newsline.poll import start_manual_check
from app.newsline.preview import PreviewError, preview_frames
from app.routes.speak import _raise_engine_http
from app.schemas import NewslinePreviewIn

router = APIRouter()
log = logging.getLogger("app.routes.newsline")
_END = object()


@router.post("/api/newsline/check")
def check_newsline_now(_admin: AdminUser) -> dict[str, str]:
    if not start_manual_check():
        raise HTTPException(status_code=409, detail="A Newsline check is already running")
    notify_home_data_changed()
    return {"result": "started"}


@router.post("/api/skip-notice/dismiss")
def dismiss_skip_notice(_admin: AdminUser) -> dict[str, bool]:
    clear_skip_notice()
    notify_home_live_status(force=True)
    return {"ok": True}


@router.post("/api/newsline/preview")
async def preview_newsline(
    req: NewslinePreviewIn, request: Request, _user: AuthUser
) -> StreamingResponse:
    try:
        sample_rate, frames = await asyncio.to_thread(
            lambda: preview_frames(
                opening_announcement_id=req.opening_announcement_id,
                station_id_announcement_id=req.station_id_announcement_id,
                closing_announcement_id=req.closing_announcement_id,
            )
        )
    except PreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        _raise_engine_http(exc, where="newsline preview")
    return StreamingResponse(
        _byte_stream(request, frames),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Preview-Framing": "name32+length32",
            "X-Sample-Rate": str(sample_rate),
            "X-Sample-Width": "2",
            "X-Channels": "1",
        },
    )


async def _byte_stream(request: Request, blocks: Iterator[bytes]) -> AsyncIterator[bytes]:
    iterator = iter(blocks)
    gen_lock = threading.Lock()

    def take_next() -> bytes | object:
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
                log.debug("Newsline preview stream close failed", exc_info=True)

    try:
        while True:
            if await request.is_disconnected():
                break
            block = await asyncio.to_thread(take_next)
            if block is _END:
                break
            if await request.is_disconnected():
                break
            if block:
                yield block
    finally:
        await asyncio.to_thread(close_gen)
