from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import timedelta

import pytest

from app.audio import PcmChunk
from app.fire import handle_fire
from app.models import Announcement, parse_schedule
from app.radio import StubRadio, transmit
from tests.fire_helpers import NOW, make_announcement, make_ctx, make_deps


def test_clear_channel_keys_ptt_waits_lead_plays_and_unkeys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    radio = StubRadio()
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(make_ctx(), deps) == "transmitted"
    assert extras["slept"] == [0.4]
    assert extras["played"] == ["play"]
    assert extras["last_runs"]
    assert extras["consumed"] == [("sched1", "2026-08-27T12:00")]
    assert ("ptt", True) in radio.events
    assert radio.events[-1] == ("ptt", False)
    assert all(e == ("busy_check", False) for e in radio.events if e[0] == "busy_check")
    assert radio.ptt is False
    assert (
        "Fire started; trigger=scheduled; schedule=Baseline ID; announcement=ID"
        in caplog.text
    )
    assert (
        "Fire transmitted; trigger=scheduled; schedule=Baseline ID; announcement=ID"
        in caplog.text
    )


def test_manual_trigger_logged_as_manual(caplog: pytest.LogCaptureFixture) -> None:
    radio = StubRadio()
    announcement = make_announcement()
    deps, _extras = make_deps(announcement, radio)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(make_ctx(trigger="manual"), deps) == "transmitted"
    assert (
        "Fire started; trigger=manual; schedule=Baseline ID; announcement=ID"
        in caplog.text
    )
    assert (
        "Fire transmitted; trigger=manual; schedule=Baseline ID; announcement=ID"
        in caplog.text
    )


def test_set_running_hooks_around_playback() -> None:
    radio = StubRadio()
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    seen: list[str] = []

    def set_running(
        announcement: Announcement,
        schedule_id: str,
        *,
        piece_label: str | None = None,
        story_index: int | None = None,
        story_total: int | None = None,
    ) -> None:
        _ = (announcement, schedule_id, piece_label, story_index, story_total)
        seen.append("start")

    deps.set_running = set_running
    deps.clear_running = lambda: seen.append("end")
    assert handle_fire(make_ctx(), deps) == "transmitted"
    assert seen == ["start", "end"]
    assert extras["played"] == ["play"]


def test_fire_renders_pcm_before_keying_ptt() -> None:
    radio = StubRadio()
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    events: list[str] = []
    played = extras["played"]
    assert isinstance(played, list)

    def synthesize(_item: Announcement) -> Iterator[PcmChunk]:
        def gen() -> Iterator[PcmChunk]:
            events.append("synth")
            yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)
            events.append("pause")
            yield PcmChunk(pcm_int16=b"\x00" * 100, sample_rate=22050)
            events.append("synth2")
            yield PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)

        return gen()

    def play(chunks: Iterable[PcmChunk]) -> None:
        events.append("play")
        played.append("play")
        assert list(chunks)

    deps.synthesize = synthesize
    deps.play_fn = play
    assert handle_fire(make_ctx(), deps) == "transmitted"
    assert events == ["synth", "pause", "synth2", "play"]
    ptt_on = next(i for i, event in enumerate(radio.events) if event == ("ptt", True))
    assert events.index("synth2") < events.index("play")
    assert radio.events[ptt_on:][0] == ("ptt", True)


def test_manual_fire_does_not_update_last_run_or_rotation() -> None:
    radio = StubRadio()
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    deps.update_schedule = False
    assert handle_fire(make_ctx(), deps) == "transmitted"
    assert extras["played"] == ["play"]
    assert extras["last_runs"] == []
    assert extras["consumed"] == []
    assert ("ptt", True) in radio.events


def test_set_running_not_called_when_busy() -> None:
    radio = StubRadio()
    radio.busy = True
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    seen: list[str] = []

    def set_running(
        announcement: Announcement,
        schedule_id: str,
        *,
        piece_label: str | None = None,
        story_index: int | None = None,
        story_total: int | None = None,
    ) -> None:
        _ = (announcement, schedule_id, piece_label, story_index, story_total)
        seen.append("start")

    deps.set_running = set_running
    deps.clear_running = lambda: seen.append("end")
    assert handle_fire(make_ctx(), deps) == "deferred"
    assert seen == []
    assert extras["consumed"] == []


def test_play_error_still_unkeys_ptt() -> None:
    radio = StubRadio()
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)

    def boom(_chunks: Iterable[PcmChunk]) -> None:
        raise RuntimeError("speaker failed")

    deps.play_fn = boom
    try:
        _ = handle_fire(make_ctx(), deps)
    except RuntimeError:
        pass
    assert ("ptt", True) in radio.events
    assert radio.events[-1] == ("ptt", False)
    assert radio.ptt is False
    assert extras["last_runs"] == []
    assert extras["consumed"] == []


def test_silence_does_not_key_or_play(caplog: pytest.LogCaptureFixture) -> None:
    radio = StubRadio()
    announcement = make_announcement()
    schedule = parse_schedule(
        {
            "id": "quiet",
            "name": "Net in progress",
            "kind": "weekly",
            "days": ["thu"],
            "slots": ["12:00"],
            "announcement_id": None,
        }
    )
    deps, extras = make_deps(announcement, radio, schedule=schedule)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(make_ctx(), deps) == "silence"
    assert extras["played"] == []
    assert extras["last_runs"]
    assert extras["consumed"] == []
    assert not any(event[0] == "ptt" for event in radio.events)
    assert "silence occupies slot" in caplog.text


def test_overlay_transmit_does_not_consume_baseline() -> None:
    radio = StubRadio()
    announcement = make_announcement(name="Tech Net")
    schedule = parse_schedule(
        {
            "id": "net",
            "name": "Weekly",
            "kind": "weekly",
            "days": ["thu"],
            "slots": ["12:00"],
            "offset_minutes": -5,
            "announcement_id": "net1",
        }
    )
    deps, extras = make_deps(announcement, radio, schedule=schedule)
    assert handle_fire(make_ctx(), deps) == "transmitted"
    assert extras["consumed"] == []


def test_busy_then_clear_defers_without_writing_last_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    radio = StubRadio()
    radio.busy = True
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(make_ctx(), deps) == "deferred"
    assert extras["deferred"]
    assert extras["played"] == []
    assert extras["last_runs"] == []
    assert extras["consumed"] == []
    assert not any(event[0] == "ptt" for event in radio.events)
    deferred = extras["deferred"]
    assert isinstance(deferred, list)
    retry_at, ctx = deferred[0]
    assert ctx.fired_at == NOW
    assert retry_at == NOW + timedelta(seconds=5)
    assert (
        "Fire deferred; trigger=scheduled; schedule=Baseline ID; announcement=ID;"
        in caplog.text
    )


def test_busy_past_deadline_drops_without_ptt(caplog: pytest.LogCaptureFixture) -> None:
    radio = StubRadio()
    radio.busy = True
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)
    ctx = make_ctx()
    ctx.deadline = NOW
    deps.now = NOW + timedelta(seconds=1)
    with caplog.at_level(logging.INFO, logger="app.fire"):
        assert handle_fire(ctx, deps) == "dropped"
    assert extras["deferred"] == []
    assert extras["played"] == []
    assert not any(event[0] == "ptt" for event in radio.events)
    assert (
        "Fire dropped; trigger=scheduled; schedule=Baseline ID; announcement=ID; "
        "channel still busy"
    ) in caplog.text


def test_transmit_uses_injected_lead_not_announcement() -> None:
    radio = StubRadio()
    radio.busy = False
    slept: list[float] = []
    transmit(
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=radio,
        play_fn=lambda _c: None,
        sleep_fn=slept.append,
        lead_seconds=0.4,
    )
    assert slept == [0.4]
    assert ("ptt", True) in radio.events
    assert radio.events[-1] == ("ptt", False)
