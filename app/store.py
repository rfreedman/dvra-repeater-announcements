from __future__ import annotations

import json
import threading
from pathlib import Path

from app.config import ANNOUNCEMENTS_PATH
from app.models import (
    Announcement,
    Rotation,
    Schedule,
    Settings,
    StoreDocument,
)
from app.schedule_logic import effective_baseline_order, next_rotation

_lock = threading.Lock()
_store: AnnouncementStore | None = None


class DataFileError(RuntimeError):
    """A data file exists but is not in a format this version understands."""


def read_versioned_json(path: Path, *, version: int, kind: str) -> dict | None:
    """Parsed JSON object, or None if the file is missing/empty.

    Raises instead of returning an empty document for an unknown format: the
    next save would otherwise overwrite the operator's data.
    """
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    raw = json.loads(text)
    if raw == {}:
        return None
    if not isinstance(raw, dict) or raw.get("version") != version:
        found = raw.get("version") if isinstance(raw, dict) else None
        raise DataFileError(
            f"{path} is not a version {version} {kind} file (found version {found!r}). "
            "Move it aside or restore a backup; refusing to overwrite it."
        )
    return raw


def write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON atomically via a same-directory .tmp file and replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def empty_document() -> StoreDocument:
    return StoreDocument()


class AnnouncementStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> StoreDocument:
        raw = read_versioned_json(self.path, version=2, kind="announcements")
        if raw is None:
            return empty_document()
        return StoreDocument.model_validate(raw)

    def _write(self, document: StoreDocument) -> None:
        write_json_atomic(self.path, document.model_dump(mode="json"))

    def document(self) -> StoreDocument:
        with _lock:
            return self._read()

    def replace(self, document: StoreDocument) -> StoreDocument:
        with _lock:
            self._write(document)
            return document

    def list_announcements(self) -> list[Announcement]:
        return self.document().announcements

    def get(self, announcement_id: str) -> Announcement | None:
        return self.document().announcement_by_id(announcement_id)

    def save_announcement(self, announcement: Announcement) -> Announcement:
        with _lock:
            document = self._read()
            items = list(document.announcements)
            replaced = False
            for index, item in enumerate(items):
                if item.id == announcement.id:
                    items[index] = announcement
                    replaced = True
                    break
            if not replaced:
                items.append(announcement)
            self._write(document.model_copy(update={"announcements": items}))
            return announcement

    def delete_announcement(self, announcement_id: str) -> bool:
        with _lock:
            document = self._read()
            users = [item.name for item in document.schedules if item.announcement_id == announcement_id]
            for item in document.announcements:
                if item.kind != "newsline":
                    continue
                if announcement_id in {
                    item.opening_announcement_id,
                    item.station_id_announcement_id,
                    item.closing_announcement_id,
                }:
                    users.append(item.name)
            if users:
                raise ValueError("Announcement is used by: " + ", ".join(users))
            kept = [item for item in document.announcements if item.id != announcement_id]
            if len(kept) == len(document.announcements):
                return False
            self._write(document.model_copy(update={"announcements": kept}))
            return True

    def list_schedules(self) -> list[Schedule]:
        return self.document().schedules

    def get_schedule(self, schedule_id: str) -> Schedule | None:
        return self.document().schedule_by_id(schedule_id)

    def save_schedule(self, schedule: Schedule) -> Schedule:
        with _lock:
            document = self._read()
            items = list(document.schedules)
            replaced = False
            for index, item in enumerate(items):
                if item.id == schedule.id:
                    schedule = schedule.model_copy(update={"last_run_at": item.last_run_at})
                    items[index] = schedule
                    replaced = True
                    break
            if not replaced:
                items.append(schedule)
            updated = document.model_copy(update={"schedules": items})
            updated = _refresh_rotation(updated)
            self._write(updated)
            return schedule

    def delete_schedule(self, schedule_id: str) -> bool:
        with _lock:
            document = self._read()
            kept = [item for item in document.schedules if item.id != schedule_id]
            if len(kept) == len(document.schedules):
                return False
            updated = document.model_copy(update={"schedules": kept})
            updated = _refresh_rotation(updated)
            self._write(updated)
            return True

    def set_enabled(self, schedule_id: str, enabled: bool) -> Schedule | None:
        with _lock:
            document = self._read()
            items: list[Schedule] = []
            found: Schedule | None = None
            for item in document.schedules:
                if item.id == schedule_id:
                    found = item.model_copy(update={"enabled": enabled})
                    items.append(found)
                else:
                    items.append(item)
            if found is None:
                return None
            updated = document.model_copy(update={"schedules": items})
            updated = _refresh_rotation(updated)
            self._write(updated)
            return found

    def set_last_run(self, announcement_id: str, schedule_id: str, when) -> None:
        with _lock:
            document = self._read()
            items: list[Schedule] = []
            found = False
            for item in document.schedules:
                if item.id == schedule_id:
                    items.append(item.model_copy(update={"last_run_at": when}))
                    found = True
                else:
                    items.append(item)
            if not found:
                return
            self._write(document.model_copy(update={"schedules": items}))

    def consume_baseline_slot(self, schedule_id: str, slot_key: str) -> None:
        with _lock:
            document = self._read()
            schedule = document.schedule_by_id(schedule_id)
            if schedule is None or schedule.kind != "baseline":
                return
            order, index, consumed = next_rotation(document, slot_key)
            self._write(
                document.model_copy(
                    update={
                        "rotation": Rotation(order=order, index=index, last_consumed_slot=consumed),
                    }
                )
            )

    def save_settings(self, settings: Settings) -> Settings:
        with _lock:
            document = self._read()
            updated = document.model_copy(update={"settings": settings})
            updated = _refresh_rotation(updated)
            self._write(updated)
            return settings


def _refresh_rotation(document: StoreDocument) -> StoreDocument:
    order = effective_baseline_order(document)
    index = document.rotation.index
    if order:
        index = index % len(order)
    else:
        index = 0
    rotation = document.rotation.model_copy(update={"order": order, "index": index})
    return document.model_copy(update={"rotation": rotation})


def get_store() -> AnnouncementStore:
    global _store
    if _store is None:
        _store = AnnouncementStore(ANNOUNCEMENTS_PATH)
    return _store


def set_store(store: AnnouncementStore | None) -> None:
    global _store
    _store = store
