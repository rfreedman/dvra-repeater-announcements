"""Newsline piece order, PTT gaps, and emergency yield. No radio hardware."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.audio import PcmChunk
from app.fire import FireContext, FireDeps, LoadedFire, handle_fire
from app.models import Announcement, Schedule, StoreDocument
from app.newsline.playlist import Piece, build_playlist
from app.newsline.session import bulletin_session
from app.radio import StubRadio

ZONE = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 23, 18, 0, tzinfo=ZONE)


def _pcm() -> list[PcmChunk]:
    return [PcmChunk(pcm_int16=b"\x00\x01", sample_rate=22050)]


def _deps(radio: StubRadio, *, synthesize, play, sleep, defer, lock, deadline=None) -> FireDeps:
    return FireDeps(
        now=NOW,
        radio=radio,
        play_fn=play,
        sleep_fn=sleep,
        schedule_defer=defer,
        playback_lock=lock,
        synthesize=synthesize,
        mark_last_run=lambda *_args: None,
        consume_baseline=lambda *_args: None,
        load=lambda: None,
        ptt_lead_seconds=0.4,
        dra_clear_seconds=0,
        dra_clear_poll_seconds=0.25,
        not_ready_deadline=deadline,
    )


def test_playlist_substitutes_station_id_and_keeps_story_order(tmp_path):
    station = Announcement(name="ID", text="This is the station.")
    opening = Announcement(name="Open", text="News follows.")
    closing = Announcement(name="Close", text="That is the news.")
    too_long = closing.model_copy(update={"too_long": True, "play_seconds": 200})
    bulletin = Announcement(
        name="Newsline",
        kind="newsline",
        station_id_announcement_id=station.id,
        opening_announcement_id=opening.id,
        closing_announcement_id=too_long.id,
        text="",
    )
    stories = tmp_path / "stories"
    stories.mkdir()
    (stories / "2554_01_intro.mp3").write_bytes(b"a")
    (stories / "2554_02_station-id.mp3").write_bytes(b"")
    (stories / "2554_03_story.mp3").write_bytes(b"b")
    document = StoreDocument(announcements=[station, opening, too_long, bulletin])
    pieces = build_playlist(bulletin, document, stories)
    assert [piece.label for piece in pieces] == ["Open", "2554_01_intro.mp3", "ID", "2554_03_story.mp3"]
    assert pieces[2].announcement is not None and pieces[2].announcement.id == station.id


def test_playlist_can_skip_a_missing_station_id(tmp_path):
    bulletin = Announcement.model_construct(name="Newsline", kind="newsline")
    stories = tmp_path / "stories"
    stories.mkdir()
    (stories / "2554_01.mp3").write_bytes(b"a")
    (stories / "2554_02_station-id.mp3").write_bytes(b"")
    (stories / "2554_03.mp3").write_bytes(b"b")
    pieces = build_playlist(bulletin, StoreDocument(), stories, require_station=False)
    assert [piece.label for piece in pieces] == ["2554_01.mp3", "2554_03.mp3"]


def test_gap_unkeys_and_lead_runs_for_each_piece(tmp_path, monkeypatch):
    from app.newsline.playback import transmit_newsline

    radio = StubRadio()
    played: list[str] = []
    bulletin = Announcement(name="News", kind="newsline", station_id_announcement_id="station", text="", ptt_gap_seconds=2)
    schedule = Schedule(name="Friday", kind="weekly", announcement_id=bulletin.id, days=["fri"], slots=["18:00"])
    pieces = [
        Piece(label="one", path=tmp_path / "one.mp3"),
        Piece(label="two", path=tmp_path / "two.mp3"),
    ]
    monkeypatch.setattr("app.newsline.playback.mp3_to_chunks", lambda _path: _pcm())
    bulletin_session.begin()
    try:
        outcome = transmit_newsline(
            _deps(
                radio,
                synthesize=lambda _item: _pcm(),
                play=lambda _chunks: played.append("audio"),
                sleep=lambda _seconds: None,
                defer=lambda *_args: None,
                lock=__import__("threading").Lock(),
            ),
            pieces,
            bulletin=bulletin,
            schedule=schedule,
            initial_deadline=NOW + timedelta(minutes=5),
        )
    finally:
        bulletin_session.end()
    assert outcome == "transmitted"
    assert played == ["audio", "audio"]
    ptt = [event for event in radio.events if event[0] == "ptt"]
    assert ptt == [("ptt", True), ("ptt", False), ("ptt", True), ("ptt", False)]


def test_not_ready_defers_until_the_slot_window_without_the_lock(monkeypatch):
    radio = StubRadio()
    deferred: list[datetime] = []
    lock = __import__("threading").Lock()
    bulletin = Announcement(name="News", kind="newsline", station_id_announcement_id="s", text="")
    schedule = Schedule(
        name="Friday",
        kind="weekly",
        announcement_id=bulletin.id,
        days=["fri"],
        slots=["18:00"],
    )
    monkeypatch.setattr("app.newsline.playlist.stories_ready", lambda _path: False)
    monkeypatch.setattr("app.newsline.state.load_state", lambda root=None: type("S", (), {"stories_dir": lambda self: None})())
    ctx = FireContext(
        announcement_id=bulletin.id,
        schedule_id=schedule.id,
        slot_key="2026-10-23T18:00",
        fired_at=NOW,
        deadline=NOW + timedelta(minutes=5),
    )
    window_end = NOW + timedelta(minutes=10)

    def load():
        return LoadedFire(announcement=bulletin, schedule=schedule, silence=False)

    deps = _deps(
        radio,
        synthesize=lambda _item: _pcm(),
        play=lambda _chunks: None,
        sleep=lambda _seconds: None,
        defer=lambda when, _ctx: deferred.append(when),
        lock=lock,
        deadline=window_end,
    )
    deps.load = load
    assert handle_fire(ctx, deps) == "deferred"
    assert deferred == [NOW + timedelta(seconds=bulletin.busy_retry_seconds)]
    assert not lock.locked()

    late = FireContext(
        announcement_id=bulletin.id,
        schedule_id=schedule.id,
        slot_key=ctx.slot_key,
        fired_at=NOW,
        deadline=window_end,
    )
    deps.now = window_end
    assert handle_fire(late, deps) == "dropped"


def test_emergency_during_a_piece_plays_before_the_next_piece(tmp_path, monkeypatch):
    from app.newsline.playback import transmit_newsline

    radio = StubRadio()
    order: list[str] = []
    station = Announcement(name="ID", text="id")
    emergency = Announcement(name="Emergency", text="Stop.")
    emergency_schedule = Schedule(name="Emergency", kind="emergency", announcement_id=emergency.id)
    bulletin = Announcement(
        name="News",
        kind="newsline",
        station_id_announcement_id=station.id,
        text="",
        ptt_gap_seconds=0,
    )
    schedule = Schedule(
        name="Friday",
        kind="weekly",
        announcement_id=bulletin.id,
        days=["fri"],
        slots=["18:00"],
    )

    def play(chunks):
        text = b"".join(chunk.pcm_int16 for chunk in chunks)
        order.append(text.decode())
        if text == b"one":
            bulletin_session.offer(emergency, emergency_schedule)

    monkeypatch.setattr(
        "app.newsline.playback.mp3_to_chunks",
        lambda path: [PcmChunk(pcm_int16=path.name.encode(), sample_rate=22050)],
    )
    pieces = [
        Piece(label="one", path=tmp_path / "one"),
        Piece(label="two", path=tmp_path / "two"),
    ]

    def synthesize(item: Announcement):
        return [PcmChunk(pcm_int16=item.name.encode(), sample_rate=22050)]

    bulletin_session.begin()
    try:
        outcome = transmit_newsline(
            _deps(
                radio,
                synthesize=synthesize,
                play=play,
                sleep=lambda _seconds: None,
                defer=lambda *_args: None,
                lock=__import__("threading").Lock(),
            ),
            pieces,
            bulletin=bulletin,
            schedule=schedule,
            initial_deadline=NOW + timedelta(minutes=5),
        )
    finally:
        bulletin_session.end()
    assert outcome == "transmitted"
    assert order == ["one", "Emergency", "two"]


def test_emergency_during_the_unkey_gap_plays_before_the_next_piece(tmp_path, monkeypatch):
    from app.newsline.playback import transmit_newsline

    radio = StubRadio()
    order: list[str] = []
    offered = {"done": False}
    emergency = Announcement(name="Emergency", text="Stop.")
    emergency_schedule = Schedule(name="Emergency", kind="emergency", announcement_id=emergency.id)
    bulletin = Announcement(
        name="News",
        kind="newsline",
        station_id_announcement_id="station",
        text="",
        ptt_gap_seconds=2,
    )
    schedule = Schedule(name="Friday", kind="weekly", announcement_id=bulletin.id, days=["fri"], slots=["18:00"])

    def sleep(_seconds: float) -> None:
        if not offered["done"]:
            offered["done"] = True
            bulletin_session.offer(emergency, emergency_schedule)

    def play(chunks):
        order.append(b"".join(chunk.pcm_int16 for chunk in chunks).decode())

    monkeypatch.setattr(
        "app.newsline.playback.mp3_to_chunks",
        lambda path: [PcmChunk(pcm_int16=path.name.encode(), sample_rate=22050)],
    )
    pieces = [
        Piece(label="one", path=tmp_path / "one"),
        Piece(label="two", path=tmp_path / "two"),
    ]
    deps = _deps(
        radio,
        synthesize=lambda item: [PcmChunk(pcm_int16=item.name.encode(), sample_rate=22050)],
        play=play,
        sleep=sleep,
        defer=lambda *_args: None,
        lock=__import__("threading").Lock(),
    )
    deps.ptt_lead_seconds = 0
    bulletin_session.begin()
    try:
        outcome = transmit_newsline(
            deps,
            pieces,
            bulletin=bulletin,
            schedule=schedule,
            initial_deadline=NOW + timedelta(minutes=5),
        )
    finally:
        bulletin_session.end()
    assert outcome == "transmitted"
    assert order == ["one", "Emergency", "two"]


def test_non_emergency_is_dropped_while_the_bulletin_holds_the_lock():
    radio = StubRadio()
    lock = __import__("threading").Lock()
    lock.acquire()
    item = Announcement(name="Net", text="Net is on.")
    schedule = Schedule(name="Friday net", kind="weekly", announcement_id=item.id, days=["fri"], slots=["18:30"])
    ctx = FireContext(
        announcement_id=item.id,
        schedule_id=schedule.id,
        slot_key="2026-10-23T18:30",
        fired_at=NOW,
        deadline=NOW + timedelta(minutes=5),
    )
    deps = _deps(
        radio,
        synthesize=lambda _item: _pcm(),
        play=lambda _chunks: None,
        sleep=lambda _seconds: None,
        defer=lambda *_args: None,
        lock=lock,
    )

    def load():
        return LoadedFire(announcement=item, schedule=schedule, silence=False)

    deps.load = load
    bulletin_session.begin()
    try:
        assert handle_fire(ctx, deps) == "skipped_lock"
    finally:
        bulletin_session.end()
        lock.release()
