"""Announcement library routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.deps import AdminUser, AuthUser
from app.home_live import notify_home_data_changed
from app.models import Announcement
from app.pcm_cache import get_pcm_cache, warm_announcement, warm_announcement_in_background
from app.scheduler import sync_jobs
from app.schemas import AnnouncementIn
from app.spoken_length import measure_announcement
from app.store import get_store

router = APIRouter()
log = logging.getLogger("app.routes.announcements")


def _warm_in_background(item: Announcement) -> None:
    if item.kind != "tts":
        return
    warm_announcement_in_background(item, warm_announcement)


def _referenced(store, announcement_id: str | None, *, role: str, previous: str | None) -> None:
    if not announcement_id:
        return
    item = store.get(announcement_id)
    if item is None or item.kind != "tts":
        raise HTTPException(status_code=400, detail=f"{role} must be an existing spoken announcement")
    if item.too_long and announcement_id != previous:
        raise HTTPException(
            status_code=400,
            detail=f"{item.name} is too long for the repeater. Shorten it and save it again.",
        )


def _from_request(req: AnnouncementIn, existing: Announcement | None) -> Announcement:
    store = get_store()
    previous_opening = existing.opening_announcement_id if existing else None
    previous_station = existing.station_id_announcement_id if existing else None
    previous_closing = existing.closing_announcement_id if existing else None
    if req.kind == "newsline":
        _referenced(store, req.opening_announcement_id, role="Opening", previous=previous_opening)
        _referenced(store, req.station_id_announcement_id, role="Station ID", previous=previous_station)
        _referenced(store, req.closing_announcement_id, role="Closing", previous=previous_closing)
    data = {
        "name": req.name,
        "kind": req.kind,
        "text": req.text,
        "voice": req.voice,
        "speed": req.speed,
        "sentence_pause": req.sentence_pause,
        "busy_retry_seconds": req.busy_retry_seconds,
        "busy_give_up_seconds": req.busy_give_up_seconds,
        "opening_announcement_id": req.opening_announcement_id,
        "station_id_announcement_id": req.station_id_announcement_id,
        "closing_announcement_id": req.closing_announcement_id,
        "ptt_gap_seconds": req.ptt_gap_seconds,
    }
    if existing is not None:
        data["id"] = existing.id
        data["play_seconds"] = existing.play_seconds
        data["too_long"] = existing.too_long
    try:
        item = Announcement.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item.kind == "tts":
        timeout = store.document().settings.repeater_timeout_seconds
        try:
            item = measure_announcement(item, timeout)
        except Exception as exc:
            from app.routes.speak import _raise_engine_http

            _raise_engine_http(exc, where="announcement timing")
        if item.too_long:
            log.warning(
                "Saved %s as too long (%.1fs)",
                item.name,
                item.play_seconds or 0,
            )
    return item


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
    item = _from_request(req, None)
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
    updated = _from_request(req, existing)
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
