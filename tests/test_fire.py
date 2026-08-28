from __future__ import annotations

import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.audio import PcmChunk
from app.fire import FireContext, FireDeps, handle_fire
from app.models import Announcement, Exclusion, Schedule
from app.radio import StubRadio, transmit

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=TZ)


def _announcement(**kwargs) -> Announcement:
    schedule = Schedule.model_validate(
        {
            "id": "sched1",
            "kind": "hourly",
            "minute": 55,
            "timezone": "America/New_York",
            **kwargs.pop("schedule", {}),
        }
    )
    return Announcement(
        id="ann1",
        name="ID",
        text="This is a test.",
        busy_retry_seconds=5,
        busy_give_up_seconds=45,
        schedules=[schedule],
        **kwargs,
    )


def _deps(announcement: Announcement, radio: StubRadio, **overrides) -> FireDeps:
    played: list[str] = []
    slept: list[float] = []
    deferred: list[tuple] = []
    last_runs: list[tuple] = []
    lock = threading.Lock()

    def play(_chunks) -> None:
        played.append("play")

    def sleep(seconds: float) -> None:
        slept.append(seconds)

    def defer(when: datetime, ctx: FireContext) -> None:
        deferred.append((when, ctx))

    def load():
        return announcement, announcement.schedules[0]

    def synthesize(_item):
        return [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)]

    deps = FireDeps(
        now=NOW,
        radio=radio,
        play_fn=play,
        sleep_fn=sleep,
        schedule_defer=defer,
        playback_lock=lock,
        synthesize=synthesize,
        mark_last_run=lambda a, s, t: last_runs.append((a, s, t)),
        load=load,
        ptt_lead_seconds=0.4,
    )
    extras = {
        "played": played,
        "slept": slept,
        "deferred": deferred,
        "last_runs": last_runs,
        "lock": lock,
    }
    for key, value in overrides.items():
        setattr(deps, key, value)
    deps.played = played  # type: ignore[attr-defined]
    return deps, extras


def _ctx() -> FireContext:
    return FireContext(
        announcement_id="ann1",
        schedule_id="sched1",
        fired_at=NOW,
        deadline=NOW + timedelta(seconds=45),
    )


def test_clear_channel_keys_ptt_waits_lead_plays_and_unkeys():
    radio = StubRadio()
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    assert handle_fire(_ctx(), deps) == "transmitted"
    assert extras["slept"] == [0.4]
    assert extras["played"] == ["play"]
    assert extras["last_runs"]
    assert radio.events == [("busy_check", False), ("ptt", True), ("ptt", False)]
    assert radio.ptt is False


def test_set_running_hooks_around_playback():
    radio = StubRadio()
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    seen: list[str] = []
    deps.set_running = lambda _item, _sid: seen.append("start")
    deps.clear_running = lambda: seen.append("end")
    assert handle_fire(_ctx(), deps) == "transmitted"
    assert seen == ["start", "end"]


def test_set_running_not_called_when_busy():
    radio = StubRadio()
    radio.busy = True
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    seen: list[str] = []
    deps.set_running = lambda _item, _sid: seen.append("start")
    deps.clear_running = lambda: seen.append("end")
    assert handle_fire(_ctx(), deps) == "deferred"
    assert seen == []


def test_play_error_still_unkeys_ptt():
    radio = StubRadio()
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)

    def boom(_chunks) -> None:
        raise RuntimeError("speaker failed")

    deps.play_fn = boom
    try:
        handle_fire(_ctx(), deps)
    except RuntimeError:
        pass
    assert ("ptt", True) in radio.events
    assert radio.events[-1] == ("ptt", False)
    assert radio.ptt is False
    assert extras["last_runs"] == []


def test_exclusion_does_not_key_or_play():
    radio = StubRadio()
    announcement = _announcement(
        schedule={"exclusions": [Exclusion(kind="day", days=["thu"]).model_dump()]}
    )
    deps, extras = _deps(announcement, radio)
    # 2026-08-27 is a Thursday
    assert handle_fire(_ctx(), deps) == "excluded"
    assert extras["played"] == []
    assert extras["last_runs"] == []
    assert not any(event[0] == "ptt" for event in radio.events)


def test_busy_then_clear_defers_without_writing_last_run():
    radio = StubRadio()
    radio.busy = True
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    assert handle_fire(_ctx(), deps) == "deferred"
    assert extras["deferred"]
    assert extras["played"] == []
    assert extras["last_runs"] == []
    assert not any(event[0] == "ptt" for event in radio.events)
    retry_at, ctx = extras["deferred"][0]
    assert ctx.fired_at == NOW
    assert retry_at == NOW + timedelta(seconds=5)


def test_busy_past_deadline_drops_without_ptt():
    radio = StubRadio()
    radio.busy = True
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    ctx = _ctx()
    ctx.deadline = NOW
    deps.now = NOW + timedelta(seconds=1)
    assert handle_fire(ctx, deps) == "dropped"
    assert extras["deferred"] == []
    assert extras["played"] == []
    assert not any(event[0] == "ptt" for event in radio.events)


def test_transmit_uses_injected_lead_not_announcement():
    radio = StubRadio()
    slept: list[float] = []
    transmit(
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=radio,
        play_fn=lambda _c: None,
        sleep_fn=slept.append,
        lead_seconds=0.4,
    )
    assert slept == [0.4]
    assert radio.events == [("ptt", True), ("ptt", False)]
