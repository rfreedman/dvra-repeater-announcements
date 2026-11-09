"""Shared typed helpers for fire / transmit unit tests."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo

from app.audio import PcmChunk
from app.fire_types import FireContext, FireDeps, LoadedFire
from app.models import Announcement, Schedule, TtsAnnouncement, parse_schedule
from app.radio import Radio

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=TZ)

class FireExtras(TypedDict):
    played: list[str]
    slept: list[float]
    deferred: list[tuple[datetime, FireContext]]
    last_runs: list[tuple[str | None, str, datetime]]
    consumed: list[tuple[str, str]]
    lock: threading.Lock


def make_announcement(**kwargs: object) -> TtsAnnouncement:
    data: dict[str, object] = {
        "id": "ann1",
        "name": "ID",
        "text": "This is a test.",
        "busy_retry_seconds": 5,
        "busy_give_up_seconds": 45,
    }
    data.update(kwargs)
    return TtsAnnouncement.model_validate(data)


def make_schedule(**kwargs: object) -> Schedule:
    data: dict[str, object] = {
        "id": "sched1",
        "name": "Baseline ID",
        "kind": "baseline",
        "announcement_id": "ann1",
    }
    data.update(kwargs)
    return parse_schedule(data)


def make_deps(
    announcement: Announcement,
    radio: Radio,
    schedule: Schedule | None = None,
    **overrides: object,
) -> tuple[FireDeps, FireExtras]:
    played: list[str] = []
    slept: list[float] = []
    deferred: list[tuple[datetime, FireContext]] = []
    last_runs: list[tuple[str | None, str, datetime]] = []
    consumed: list[tuple[str, str]] = []
    lock = threading.Lock()
    row = schedule or make_schedule()

    def play(_chunks: Iterable[PcmChunk]) -> None:
        played.append("play")

    def sleep(seconds: float) -> None:
        slept.append(seconds)

    def defer(when: datetime, ctx: FireContext) -> None:
        deferred.append((when, ctx))

    def load() -> LoadedFire:
        return LoadedFire(announcement=announcement, schedule=row, silence=row.is_silence)

    def synthesize(_item: Announcement) -> list[PcmChunk]:
        return [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    def mark_last_run(ann_id: str | None, slot_key: str, when: datetime) -> None:
        last_runs.append((ann_id, slot_key, when))

    def consume_baseline(sid: str, key: str) -> None:
        consumed.append((sid, key))

    deps = FireDeps(
        now=NOW,
        radio=radio,
        play_fn=play,
        sleep_fn=sleep,
        schedule_defer=defer,
        playback_lock=lock,
        synthesize=synthesize,
        mark_last_run=mark_last_run,
        consume_baseline=consume_baseline,
        load=load,
        ptt_lead_seconds=0.4,
    )
    extras: FireExtras = {
        "played": played,
        "slept": slept,
        "deferred": deferred,
        "last_runs": last_runs,
        "consumed": consumed,
        "lock": lock,
    }
    for key, value in overrides.items():
        setattr(deps, key, value)
    return deps, extras


def make_ctx(*, trigger: Literal["scheduled", "manual"] = "scheduled") -> FireContext:
    return FireContext(
        announcement_id="ann1",
        schedule_id="sched1",
        slot_key="2026-08-27T12:00",
        fired_at=NOW,
        deadline=NOW + timedelta(seconds=45),
        trigger=trigger,
    )

