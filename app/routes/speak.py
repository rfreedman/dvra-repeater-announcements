"""Voice catalog and preview speech."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.audio import PcmChunk
from app.deps import AuthUser
from app.engines.base import VoiceError
from app.registry import get_registry
from app.schemas import PrepareRequest, SpeakRequest

router = APIRouter()
registry = get_registry()
_END = object()


@router.get("/api/voices")
def voices(_user: AuthUser) -> dict[str, object]:
    return registry.voices_payload()


@router.post("/api/prepare")
def prepare(req: PrepareRequest, _user: AuthUser) -> dict[str, str]:
    try:
        voice_id = registry.prepare(req.voice)
    except VoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"voice": voice_id}


@router.post("/api/speak")
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
