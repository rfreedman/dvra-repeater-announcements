from __future__ import annotations

import json
import threading
from pathlib import Path

from pydantic import TypeAdapter

from app.config import ANNOUNCEMENTS_PATH
from app.models import Announcement, Schedule, new_id

_ADAPTER = TypeAdapter(list[Announcement])
_lock = threading.Lock()
_store: AnnouncementStore | None = None


class AnnouncementStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> list[Announcement]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        if isinstance(raw, dict):
            raw = raw.get("announcements", [])
        return _ADAPTER.validate_python(raw)

    def _write(self, items: list[Announcement]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"announcements": [item.model_dump(mode="json") for item in items]},
            indent=2,
        )
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def list_announcements(self) -> list[Announcement]:
        with _lock:
            return self._read()

    def get(self, announcement_id: str) -> Announcement | None:
        for item in self.list_announcements():
            if item.id == announcement_id:
                return item
        return None

    def save(self, announcement: Announcement) -> Announcement:
        with _lock:
            items = self._read()
            for index, item in enumerate(items):
                if item.id == announcement.id:
                    items[index] = announcement
                    self._write(items)
                    return announcement
            items.append(announcement)
            self._write(items)
            return announcement

    def upsert(self, announcement: Announcement) -> Announcement:
        return self.save(announcement)

    def delete_announcement(self, announcement_id: str) -> bool:
        with _lock:
            items = self._read()
            kept = [item for item in items if item.id != announcement_id]
            if len(kept) == len(items):
                return False
            self._write(kept)
            return True

    def upsert_schedule(self, announcement_id: str, schedule: Schedule) -> Announcement | None:
        with _lock:
            items = self._read()
            for index, item in enumerate(items):
                if item.id != announcement_id:
                    continue
                schedules = list(item.schedules)
                replaced = False
                for s_index, existing in enumerate(schedules):
                    if existing.id == schedule.id:
                        schedule.last_run_at = existing.last_run_at
                        schedules[s_index] = schedule
                        replaced = True
                        break
                if not replaced:
                    if not schedule.id:
                        schedule.id = new_id()
                    schedules.append(schedule)
                updated = item.model_copy(update={"schedules": schedules})
                items[index] = updated
                self._write(items)
                return updated
            return None

    def add_schedule(self, announcement_id: str, schedule: Schedule) -> Announcement | None:
        return self.upsert_schedule(announcement_id, schedule)

    def delete_schedule(self, announcement_id: str, schedule_id: str) -> Announcement | None:
        with _lock:
            items = self._read()
            for index, item in enumerate(items):
                if item.id != announcement_id:
                    continue
                schedules = [row for row in item.schedules if row.id != schedule_id]
                if len(schedules) == len(item.schedules):
                    return None
                updated = item.model_copy(update={"schedules": schedules})
                items[index] = updated
                self._write(items)
                return updated
            return None

    def set_enabled(self, announcement_id: str, schedule_id: str, enabled: bool) -> Announcement | None:
        with _lock:
            items = self._read()
            for index, item in enumerate(items):
                if item.id != announcement_id:
                    continue
                schedules = []
                found = False
                for row in item.schedules:
                    if row.id == schedule_id:
                        schedules.append(row.model_copy(update={"enabled": enabled}))
                        found = True
                    else:
                        schedules.append(row)
                if not found:
                    return None
                updated = item.model_copy(update={"schedules": schedules})
                items[index] = updated
                self._write(items)
                return updated
            return None

    def set_last_run(self, announcement_id: str, schedule_id: str, when) -> None:
        with _lock:
            items = self._read()
            for index, item in enumerate(items):
                if item.id != announcement_id:
                    continue
                schedules = []
                found = False
                for row in item.schedules:
                    if row.id == schedule_id:
                        schedules.append(row.model_copy(update={"last_run_at": when}))
                        found = True
                    else:
                        schedules.append(row)
                if not found:
                    return
                items[index] = item.model_copy(update={"schedules": schedules})
                self._write(items)
                return


def get_store() -> AnnouncementStore:
    global _store
    if _store is None:
        _store = AnnouncementStore(ANNOUNCEMENTS_PATH)
    return _store


def set_store(store: AnnouncementStore | None) -> None:
    global _store
    _store = store
