from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.audio import PcmChunk
from app.config import MAX_TEXT_CHARS, STATIC_DIR
from app.engines.base import VoiceError
from app.registry import get_registry

app = FastAPI(title="TTS Stream", version="1.0.0")
registry = get_registry()
_END = object()


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    engine: str | None = None
    voice: str | None = None
    speed: float = Field(default=1.0, gt=0.25, le=2.0)


class PrepareRequest(BaseModel):
    engine: str | None = None
    voice: str | None = None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/voices")
def voices() -> dict[str, object]:
    return registry.voices_payload()


@app.post("/api/prepare")
def prepare(req: PrepareRequest) -> dict[str, str]:
    try:
        engine_id, voice_id = registry.prepare(req.engine, req.voice)
    except VoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"engine": engine_id, "voice": voice_id}


@app.post("/api/speak")
async def speak(req: SpeakRequest, request: Request) -> StreamingResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    try:
        engine, voice_id, sample_rate, chunks = await asyncio.to_thread(
            registry.synthesize,
            text,
            req.engine,
            req.voice,
            req.speed,
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
            "X-Engine": engine.id,
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
