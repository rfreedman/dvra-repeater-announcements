"""While a Newsline bulletin holds the radio, an emergency can take the next gap."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from app.models import Announcement, Schedule


@dataclass
class PendingEmergency:
    announcement: Announcement
    schedule: Schedule


class BulletinSession:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._active = False
        self._pending: PendingEmergency | None = None

    @property
    def active(self) -> bool:
        with self._guard:
            return self._active

    def begin(self) -> None:
        with self._guard:
            self._active = True
            self._pending = None

    def end(self) -> None:
        with self._guard:
            self._active = False
            self._pending = None

    def offer(self, announcement: Announcement, schedule: Schedule) -> bool:
        with self._guard:
            if not self._active or schedule.kind != "emergency":
                return False
            self._pending = PendingEmergency(announcement=announcement, schedule=schedule)
            return True

    def waiting(self) -> bool:
        with self._guard:
            return self._pending is not None

    def take(self) -> PendingEmergency | None:
        with self._guard:
            pending = self._pending
            self._pending = None
            return pending


bulletin_session = BulletinSession()
