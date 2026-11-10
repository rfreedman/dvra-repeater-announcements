"""Announcement library routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.deps import AdminUser, AuthUser
from app.live_notify import notify_home_data_changed
from app.models import Announcement, validate_announcement
from app.pcm_cache import (
    get_pcm_cache,
    measure_announcement,
    warm_announcement,
    warm_announcement_in_background,
)
from app.scheduler import sync_jobs
from app.schemas import AnnouncementIn, AnnouncementsListOut
from app.store import AnnouncementStore, get_store

router = APIRouter()
log = logging.getLogger("app.routes.announcements")


def _warm_in_background(item: Announcement) -> None:
    if item.kind != "tts":
        return
    _ = warm_announcement_in_background(item, warm_announcement)


def _referenced(
    store: AnnouncementStore, announcement_id: str | None, *, role: str, previous: str | None
) -> None:
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
    previous_opening = (
        existing.opening_announcement_id if existing is not None and existing.kind == "newsline" else None
    )
    previous_station = (
        existing.station_id_announcement_id
        if existing is not None and existing.kind == "newsline"
        else None
    )
    previous_closing = (
        existing.closing_announcement_id if existing is not None and existing.kind == "newsline" else None
    )
    previous_resume = (
        existing.resume_announcement_id if existing is not None and existing.kind == "newsline" else None
    )
    if req.kind == "newsline":
        _referenced(store, req.opening_announcement_id, role="Opening", previous=previous_opening)
        _referenced(store, req.station_id_announcement_id, role="Station ID", previous=previous_station)
        _referenced(store, req.closing_announcement_id, role="Closing", previous=previous_closing)
        _referenced(store, req.resume_announcement_id, role="Resume", previous=previous_resume)
        data: dict[str, object] = {
            "name": req.name,
            "kind": "newsline",
            "busy_retry_seconds": req.busy_retry_seconds,
            "busy_give_up_seconds": req.busy_give_up_seconds,
            "opening_announcement_id": req.opening_announcement_id,
            "station_id_announcement_id": req.station_id_announcement_id,
            "closing_announcement_id": req.closing_announcement_id,
            "resume_announcement_id": req.resume_announcement_id,
            "ptt_gap_seconds": req.ptt_gap_seconds,
        }
    else:
        data = {
            "name": req.name,
            "kind": "tts",
            "text": req.text,
            "voice": req.voice,
            "speed": req.speed,
            "sentence_pause": req.sentence_pause,
            "use_phoneme_dictionary": req.use_phoneme_dictionary,
            "busy_retry_seconds": req.busy_retry_seconds,
            "busy_give_up_seconds": req.busy_give_up_seconds,
        }
        if existing is not None and existing.kind == "tts":
            data["play_seconds"] = existing.play_seconds
            data["too_long"] = existing.too_long
    if existing is not None:
        data["id"] = existing.id
    try:
        item = validate_announcement(data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item.kind != "tts":
        return item
    timeout = store.document().settings.repeater_timeout_seconds
    try:
        measured = measure_announcement(item, timeout)
    except Exception as exc:
        from app.routes.speak import raise_engine_http

        raise_engine_http(exc, where="announcement timing")
    if measured.kind == "tts" and measured.too_long:
        log.warning(
            "Saved %s as too long (%.1fs)",
            measured.name,
            measured.play_seconds or 0,
        )
    return measured


@router.get("/api/announcements")
def list_announcements(_user: AuthUser) -> AnnouncementsListOut:
    return AnnouncementsListOut(announcements=get_store().list_announcements())


@router.get("/api/announcements/{announcement_id}")
def get_announcement(announcement_id: str, _user: AuthUser) -> Announcement:
    item = get_store().get(announcement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return item


@router.post("/api/announcements")
def create_announcement(req: AnnouncementIn, _admin: AdminUser) -> Announcement:
    item = _from_request(req, None)
    stored = get_store().save_announcement(item)
    _warm_in_background(stored)
    notify_home_data_changed()
    return stored


@router.put("/api/announcements/{announcement_id}")
def update_announcement(
    announcement_id: str, req: AnnouncementIn, _admin: AdminUser
) -> Announcement:
    store = get_store()
    existing = store.get(announcement_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    updated = _from_request(req, existing)
    stored = store.save_announcement(updated)
    _warm_in_background(stored)
    sync_jobs()
    notify_home_data_changed()
    return stored


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
