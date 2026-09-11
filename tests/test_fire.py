from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.audio import PcmChunk
from app.fire import FireContext, FireDeps, LoadedFire, handle_fire
from app.models import Announcement, Schedule
from app.radio import StubRadio, transmit

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=TZ)


def _announcement(**kwargs) -> Announcement:
    data = {
        "id": "ann1",
        "name": "ID",
        "text": "This is a test.",
        "busy_retry_seconds": 5,
        "busy_give_up_seconds": 45,
    }
    data.update(kwargs)
    return Announcement(**data)


def _schedule(**kwargs) -> Schedule:
    return Schedule.model_validate(
        {
            "id": "sched1",
            "name": "Baseline ID",
            "kind": "baseline",
            "announcement_id": "ann1",
            **kwargs,
        }
    )


def _deps(announcement: Announcement, radio: StubRadio, schedule: Schedule | None = None, **overrides) -> tuple:
    played: list[str] = []
    slept: list[float] = []
    deferred: list[tuple] = []
    last_runs: list[tuple] = []
    consumed: list[tuple] = []
    lock = threading.Lock()
    row = schedule or _schedule()

    def play(_chunks) -> None:
        played.append("play")

    def sleep(seconds: float) -> None:
        slept.append(seconds)

    def defer(when: datetime, ctx: FireContext) -> None:
        deferred.append((when, ctx))

    def load():
        return LoadedFire(announcement=announcement, schedule=row, silence=row.is_silence)

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
        consume_baseline=lambda sid, key: consumed.append((sid, key)),
        load=load,
        ptt_lead_seconds=0.4,
    )
    extras = {
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


def _ctx() -> FireContext:
    return FireContext(
        announcement_id="ann1",
        schedule_id="sched1",
        slot_key="2026-08-27T12:00",
        fired_at=NOW,
        deadline=NOW + timedelta(seconds=45),
    )


def test_clear_channel_keys_ptt_waits_lead_plays_and_unkeys(caplog):
    radio = StubRadio()
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(_ctx(), deps) == "transmitted"
    assert extras["slept"] == [0.4]
    assert extras["played"] == ["play"]
    assert extras["last_runs"]
    assert extras["consumed"] == [("sched1", "2026-08-27T12:00")]
    assert radio.events == [("busy_check", False), ("ptt", True), ("ptt", False)]
    assert radio.ptt is False
    assert "Fire transmitted; ID" in caplog.text


def test_set_running_hooks_around_playback():
    radio = StubRadio()
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    seen: list[str] = []
    deps.set_running = lambda _item, _sid: seen.append("start")
    deps.clear_running = lambda: seen.append("end")
    assert handle_fire(_ctx(), deps) == "transmitted"
    assert seen == ["start", "end"]


def test_fire_renders_pcm_before_keying_ptt():
    radio = StubRadio()
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    events: list[str] = []

    def synthesize(_item):
        def gen():
            events.append("synth")
            yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)
            events.append("pause")
            yield PcmChunk(pcm_int16=b"\x00" * 100, sample_rate=22050)
            events.append("synth2")
            yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)

        return gen()

    def play(chunks) -> None:
        events.append("play")
        extras["played"].append("play")
        assert list(chunks)

    deps.synthesize = synthesize
    deps.play_fn = play
    assert handle_fire(_ctx(), deps) == "transmitted"
    assert events == ["synth", "pause", "synth2", "play"]
    ptt_on = next(i for i, event in enumerate(radio.events) if event == ("ptt", True))
    assert events.index("synth2") < events.index("play")
    assert radio.events[ptt_on:][0] == ("ptt", True)


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
    assert extras["consumed"] == []


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
    assert extras["consumed"] == []


def test_silence_does_not_key_or_play(caplog):
    radio = StubRadio()
    announcement = _announcement()
    schedule = Schedule.model_validate(
        {
            "id": "quiet",
            "name": "Net in progress",
            "kind": "weekly",
            "days": ["thu"],
            "slots": ["12:00"],
            "announcement_id": None,
        }
    )
    deps, extras = _deps(announcement, radio, schedule=schedule)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(_ctx(), deps) == "silence"
    assert extras["played"] == []
    assert extras["last_runs"]
    assert extras["consumed"] == []
    assert not any(event[0] == "ptt" for event in radio.events)
    assert "silence occupies slot" in caplog.text


def test_overlay_transmit_does_not_consume_baseline():
    radio = StubRadio()
    announcement = _announcement(name="Tech Net")
    schedule = Schedule.model_validate(
        {
            "id": "net",
            "name": "Weekly Net",
            "kind": "weekly",
            "days": ["thu"],
            "slots": ["12:00"],
            "offset_minutes": -5,
            "announcement_id": "net1",
        }
    )
    deps, extras = _deps(announcement, radio, schedule=schedule)
    assert handle_fire(_ctx(), deps) == "transmitted"
    assert extras["consumed"] == []


def test_busy_then_clear_defers_without_writing_last_run(caplog):
    radio = StubRadio()
    radio.busy = True
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(_ctx(), deps) == "deferred"
    assert extras["deferred"]
    assert extras["played"] == []
    assert extras["last_runs"] == []
    assert extras["consumed"] == []
    assert not any(event[0] == "ptt" for event in radio.events)
    retry_at, ctx = extras["deferred"][0]
    assert ctx.fired_at == NOW
    assert retry_at == NOW + timedelta(seconds=5)
    assert "Fire deferred; ID;" in caplog.text


def test_busy_past_deadline_drops_without_ptt(caplog):
    radio = StubRadio()
    radio.busy = True
    announcement = _announcement()
    deps, extras = _deps(announcement, radio)
    ctx = _ctx()
    ctx.deadline = NOW
    deps.now = NOW + timedelta(seconds=1)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(ctx, deps) == "dropped"
    assert extras["deferred"] == []
    assert extras["played"] == []
    assert not any(event[0] == "ptt" for event in radio.events)
    assert "Fire dropped; ID; channel still busy after" in caplog.text


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
