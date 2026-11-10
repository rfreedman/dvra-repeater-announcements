"""Phoneme dictionary and IPA lookup."""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import TEXT_CHARS_CEILING, enforce_text_limit
from app.deps import AdminUser, AuthUser
from app.json_io import DataFileError
from app.model_common import normalize_announcement_text
from app.phoneme_dict import get_phoneme_dictionary
from app.phoneme_text import PhonemeReplacement, PhonemeTextError, matching_entries
from app.routes.speak import raise_engine_http
from app.tts.ipa import ipa_for_text

router = APIRouter()
log = logging.getLogger("app.routes.phonemes")


class PhonemeEntryIn(BaseModel):
    spelling: str = Field(max_length=200)
    ipa: str = Field(default="", max_length=2000)
    alternate: str = Field(default="", max_length=2000)


class PhonemeEntryOut(BaseModel):
    spelling: str
    ipa: str = ""
    alternate: str = ""


class PhonemeDictionaryIn(BaseModel):
    entries: list[PhonemeEntryIn] = Field(default_factory=list, max_length=500)


class PhonemeDictionaryOut(BaseModel):
    entries: list[PhonemeEntryOut]


class IpaIn(BaseModel):
    text: str = Field(min_length=1, max_length=TEXT_CHARS_CEILING)

    def cleaned(self) -> str:
        return enforce_text_limit(normalize_announcement_text(self.text))


class IpaOut(BaseModel):
    ipa: str


def _rows(entries: list[PhonemeEntryIn]) -> list[tuple[str, str, str]]:
    return [(entry.spelling, entry.ipa, entry.alternate) for entry in entries]


def schedule_dictionary_refresh() -> None:
    """Re-render spoken announcements that use the dictionary, off the request."""
    thread = threading.Thread(
        target=_refresh_dictionary_users,
        name="phoneme-dictionary-refresh",
        daemon=True,
    )
    thread.start()


def _refresh_dictionary_users() -> None:
    from app.live_notify import notify_home_data_changed
    from app.pcm_cache import measure_announcement
    from app.store import get_store

    store = get_store()
    timeout = store.document().settings.repeater_timeout_seconds
    changed = False
    for item in store.list_announcements():
        if item.kind != "tts":
            continue
        if not item.use_phoneme_dictionary:
            continue
        try:
            measured = measure_announcement(item, timeout)
        except Exception:
            log.exception("Failed to re-render %s after a dictionary change", item.name)
            continue
        if measured.kind != "tts":
            continue
        if measured.play_seconds == item.play_seconds and measured.too_long == item.too_long:
            continue
        _ = store.save_announcement(measured)
        changed = True
    if changed:
        notify_home_data_changed()


@router.get("/api/phonemes")
def list_phonemes(_user: AuthUser) -> PhonemeDictionaryOut:
    try:
        entries = get_phoneme_dictionary().compiled()
    except DataFileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PhonemeDictionaryOut(
        entries=[_entry_out(entry) for entry in entries]
    )


@router.put("/api/phonemes")
def replace_phonemes(body: PhonemeDictionaryIn, _admin: AdminUser) -> PhonemeDictionaryOut:
    try:
        entries = get_phoneme_dictionary().replace(_rows(body.entries))
    except PhonemeTextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DataFileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    schedule_dictionary_refresh()
    return PhonemeDictionaryOut(
        entries=[_entry_out(entry) for entry in entries]
    )


@router.post("/api/phonemes/ipa")
def pronounce(body: IpaIn, _user: AuthUser) -> IpaOut:
    try:
        text = body.cleaned()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return IpaOut(ipa=ipa_for_text(text))
    except Exception as exc:
        raise_engine_http(exc, where="ipa")


@router.post("/api/phonemes/matches")
def match_text(body: IpaIn, _user: AuthUser) -> PhonemeDictionaryOut:
    try:
        text = body.cleaned()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        entries = get_phoneme_dictionary().compiled()
    except DataFileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    matched = matching_entries(text, entries, use_dictionary=True)
    return PhonemeDictionaryOut(
        entries=[_entry_out(entry) for entry in matched]
    )


def _entry_out(entry: PhonemeReplacement) -> PhonemeEntryOut:
    return PhonemeEntryOut(spelling=entry.spelling, ipa=entry.ipa, alternate=entry.alternate)
