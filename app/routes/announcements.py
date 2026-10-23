"""Announcement library routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import AdminUser, AuthUser
from app.home_live import notify_home_data_changed
from app.models import Announcement
from app.pcm_cache import get_pcm_cache, warm_announcement, warm_announcement_in_background
from app.scheduler import sync_jobs
from app.schemas import AnnouncementIn
from app.store import get_store

router = APIRouter()


def _warm_in_background(item: Announcement) -> None:
    warm_announcement_in_background(item, warm_announcement)


@router.get("/api/announcements")
def list_announcements(_user: AuthUser) -> dict[str, object]:
    return {"announcements": [item.model_dump(mode="json") for item in get_store().list_announcements()]}


@router.get("/api/announcements/{announcement_id}")
def get_announcement(announcement_id: str, _user: AuthUser) -> dict[str, object]:
    item = get_store().get(announcement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return item.model_dump(mode="json")


@router.post("/api/announcements")
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


@router.put("/api/announcements/{announcement_id}")
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


@router.delete("/api/announcements/{announcement_id}")
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
