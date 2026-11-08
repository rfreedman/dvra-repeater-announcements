"""Newsline piece order, PTT gaps, and emergency yield. No radio hardware."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.audio import PcmChunk
from app.fire import FireContext, FireDeps, LoadedFire, handle_fire
from app.models import (
    NewslineAnnouncement,
    StoreDocument,
    TtsAnnouncement,
    parse_announcement as Announcement,
    parse_schedule as Schedule,
)
from app.newsline.playlist import Piece, build_playlist, resume_piece
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
    bulletin = NewslineAnnouncement.model_construct(name="Newsline", kind="newsline")
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


def test_resume_plays_after_cos_busy_between_pieces(tmp_path, monkeypatch):
    from app.newsline.playback import ClearWait, transmit_newsline

    radio = StubRadio()
    order: list[str] = []
    resume_item = Announcement(name="Back", text="We return to Newsline.")
    resume = Piece(label=resume_item.name, announcement=resume_item)
    bulletin = Announcement(
        name="News",
        kind="newsline",
        station_id_announcement_id="station",
        text="",
        ptt_gap_seconds=2,
    )
    schedule = Schedule(name="Friday", kind="weekly", announcement_id=bulletin.id, days=["fri"], slots=["18:00"])
    waits = {"n": 0}

    def wait_until_clear(_deps, *, deadline, gap_seconds=0.0):
        waits["n"] += 1
        if waits["n"] == 1:
            return ClearWait("clear", False)
        return ClearWait("clear", True)

    monkeypatch.setattr("app.newsline.playback.wait_until_clear", wait_until_clear)
    monkeypatch.setattr(
        "app.newsline.playback.mp3_to_chunks",
        lambda path: [PcmChunk(pcm_int16=path.name.encode(), sample_rate=22050)],
    )
    pieces = [
        Piece(label="one", path=tmp_path / "one"),
        Piece(label="two", path=tmp_path / "two"),
    ]
    bulletin_session.begin()
    try:
        outcome = transmit_newsline(
            _deps(
                radio,
                synthesize=lambda item: [PcmChunk(pcm_int16=item.name.encode(), sample_rate=22050)],
                play=lambda chunks: order.append(b"".join(c.pcm_int16 for c in chunks).decode()),
                sleep=lambda _seconds: None,
                defer=lambda *_args: None,
                lock=__import__("threading").Lock(),
            ),
            pieces,
            bulletin=bulletin,
            schedule=schedule,
            initial_deadline=NOW + timedelta(minutes=5),
            resume=resume,
        )
    finally:
        bulletin_session.end()
    assert outcome == "transmitted"
    # Resume + next story share one keyed transmission (no PTT drop between).
    assert order == ["one", "Backtwo"]
    ptt = [event for event in radio.events if event[0] == "ptt"]
    assert ptt == [("ptt", True), ("ptt", False), ("ptt", True), ("ptt", False)]


def test_resume_skipped_between_opening_and_first_story(tmp_path, monkeypatch):
    from app.newsline.playback import ClearWait, transmit_newsline

    radio = StubRadio()
    order: list[str] = []
    opening = Announcement(name="Open", text="News follows.")
    resume_item = Announcement(name="Back", text="We return to Newsline.")
    resume = Piece(label=resume_item.name, announcement=resume_item)
    bulletin = Announcement(
        name="News",
        kind="newsline",
        station_id_announcement_id="station",
        opening_announcement_id=opening.id,
        text="",
        ptt_gap_seconds=2,
    )
    schedule = Schedule(name="Friday", kind="weekly", announcement_id=bulletin.id, days=["fri"], slots=["18:00"])
    waits = {"n": 0}

    def wait_until_clear(_deps, *, deadline, gap_seconds=0.0):
        waits["n"] += 1
        if waits["n"] == 1:
            return ClearWait("clear", False)
        return ClearWait("clear", True)

    monkeypatch.setattr("app.newsline.playback.wait_until_clear", wait_until_clear)
    monkeypatch.setattr(
        "app.newsline.playback.mp3_to_chunks",
        lambda path: [PcmChunk(pcm_int16=path.name.encode(), sample_rate=22050)],
    )
    pieces = [
        Piece(label=opening.name, announcement=opening),
        Piece(label="one", path=tmp_path / "one"),
    ]
    bulletin_session.begin()
    try:
        outcome = transmit_newsline(
            _deps(
                radio,
                synthesize=lambda item: [PcmChunk(pcm_int16=item.name.encode(), sample_rate=22050)],
                play=lambda chunks: order.append(b"".join(c.pcm_int16 for c in chunks).decode()),
                sleep=lambda _seconds: None,
                defer=lambda *_args: None,
                lock=__import__("threading").Lock(),
            ),
            pieces,
            bulletin=bulletin,
            schedule=schedule,
            initial_deadline=NOW + timedelta(minutes=5),
            resume=resume,
        )
    finally:
        bulletin_session.end()
    assert outcome == "transmitted"
    assert order == ["Open", "one"]


def test_resume_is_not_played_when_the_channel_stays_clear(tmp_path, monkeypatch):
    from app.newsline.playback import transmit_newsline

    radio = StubRadio()
    order: list[str] = []
    resume_item = Announcement(name="Back", text="We return to Newsline.")
    bulletin = Announcement(
        name="News",
        kind="newsline",
        station_id_announcement_id="station",
        text="",
        ptt_gap_seconds=0,
    )
    schedule = Schedule(name="Friday", kind="weekly", announcement_id=bulletin.id, days=["fri"], slots=["18:00"])
    monkeypatch.setattr(
        "app.newsline.playback.mp3_to_chunks",
        lambda path: [PcmChunk(pcm_int16=path.name.encode(), sample_rate=22050)],
    )
    bulletin_session.begin()
    try:
        outcome = transmit_newsline(
            _deps(
                radio,
                synthesize=lambda item: [PcmChunk(pcm_int16=item.name.encode(), sample_rate=22050)],
                play=lambda chunks: order.append(b"".join(c.pcm_int16 for c in chunks).decode()),
                sleep=lambda _seconds: None,
                defer=lambda *_args: None,
                lock=__import__("threading").Lock(),
            ),
            [
                Piece(label="one", path=tmp_path / "one"),
                Piece(label="two", path=tmp_path / "two"),
            ],
            bulletin=bulletin,
            schedule=schedule,
            initial_deadline=NOW + timedelta(minutes=5),
            resume=Piece(label=resume_item.name, announcement=resume_item),
        )
    finally:
        bulletin_session.end()
    assert outcome == "transmitted"
    assert order == ["one", "two"]


def test_resume_piece_omits_too_long_and_missing():
    station = Announcement(name="ID", text="id")
    too_long = Announcement(name="Back", text="We return.", too_long=True)
    bulletin = Announcement(
        name="News",
        kind="newsline",
        station_id_announcement_id=station.id,
        resume_announcement_id=too_long.id,
        text="",
    )
    document = StoreDocument(announcements=[station, too_long, bulletin])
    assert resume_piece(bulletin, document) is None
    bulletin = bulletin.model_copy(update={"resume_announcement_id": None})
    assert resume_piece(bulletin, document) is None


def test_wait_until_clear_marks_cos_busy():
    from app.newsline.playback import wait_until_clear

    radio = StubRadio()
    radio.busy_sequence = [True, False, False]
    deps = _deps(
        radio,
        synthesize=lambda _item: _pcm(),
        play=lambda _chunks: None,
        sleep=lambda _seconds: None,
        defer=lambda *_args: None,
        lock=__import__("threading").Lock(),
    )
    bulletin_session.begin()
    try:
        wait = wait_until_clear(deps, deadline=None, gap_seconds=0.0)
    finally:
        bulletin_session.end()
    assert wait.outcome == "clear"
    assert wait.interrupted is True


def test_wait_until_clear_publishes_busy_then_playing_phases():
    from app.newsline.playback import wait_until_clear

    radio = StubRadio()
    radio.busy_sequence = [True, False, False]
    phases: list[str] = []

    def set_running_wait(*, phase, clear_seconds=0.0, clear_remaining_seconds=None, force=False):
        phases.append(phase)

    deps = _deps(
        radio,
        synthesize=lambda _item: _pcm(),
        play=lambda _chunks: None,
        sleep=lambda _seconds: None,
        defer=lambda *_args: None,
        lock=__import__("threading").Lock(),
    )
    deps.set_running_wait = set_running_wait
    bulletin_session.begin()
    try:
        wait = wait_until_clear(deps, deadline=None, gap_seconds=0.0)
    finally:
        bulletin_session.end()
    assert wait.outcome == "clear"
    assert wait.interrupted is True
    assert phases[0] == "channel_busy"
    assert phases[-1] == "playing"


def test_transmit_newsline_publishes_piece_progress(tmp_path, monkeypatch):
    from app.newsline.playback import transmit_newsline

    radio = StubRadio()
    opening = Announcement(name="Open", text="News follows.")
    closing = Announcement(name="Close", text="That is the news.")
    marks: list[dict] = []

    def set_running(_announcement, _schedule_id, **kwargs):
        marks.append(dict(kwargs))

    monkeypatch.setattr(
        "app.newsline.playback.mp3_to_chunks",
        lambda path: [PcmChunk(pcm_int16=path.name.encode(), sample_rate=22050)],
    )
    bulletin = Announcement(
        name="News",
        kind="newsline",
        station_id_announcement_id="station",
        text="",
        ptt_gap_seconds=0,
    )
    schedule = Schedule(name="Friday", kind="weekly", announcement_id=bulletin.id, days=["fri"], slots=["18:00"])
    pieces = [
        Piece(label=opening.name, announcement=opening),
        Piece(label="one.mp3", path=tmp_path / "one.mp3"),
        Piece(label="two.mp3", path=tmp_path / "two.mp3"),
        Piece(label=closing.name, announcement=closing),
    ]
    deps = _deps(
        radio,
        synthesize=lambda item: [PcmChunk(pcm_int16=item.name.encode(), sample_rate=22050)],
        play=lambda _chunks: None,
        sleep=lambda _seconds: None,
        defer=lambda *_args: None,
        lock=__import__("threading").Lock(),
    )
    deps.set_running = set_running
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
    assert {"piece_label": "Open", "story_index": None, "story_total": None} in marks
    assert {"piece_label": "one.mp3", "story_index": 1, "story_total": 2} in marks
    assert {"piece_label": "two.mp3", "story_index": 2, "story_total": 2} in marks
    assert {"piece_label": "Close", "story_index": None, "story_total": None} in marks


def test_unavailable_newsline_does_not_defer_when_load_still_returns_it(monkeypatch):
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
    monkeypatch.setattr(
        "app.newsline.playlist.newsline_unavailable_reason",
        lambda state=None: "Newsline is not available",
    )
    ctx = FireContext(
        announcement_id=bulletin.id,
        schedule_id=schedule.id,
        slot_key="2026-10-23T18:00",
        fired_at=NOW,
        deadline=NOW + timedelta(minutes=5),
        trigger="scheduled",
    )

    def load():
        return LoadedFire(announcement=bulletin, schedule=schedule, silence=False)

    deps = _deps(
        radio,
        synthesize=lambda _item: _pcm(),
        play=lambda _chunks: None,
        sleep=lambda _seconds: None,
        defer=lambda when, _ctx: deferred.append(when),
        lock=lock,
        deadline=NOW + timedelta(minutes=10),
    )
    deps.load = load
    assert handle_fire(ctx, deps) == "dropped"
    assert deferred == []
    assert not lock.locked()


def test_unavailable_newsline_slot_transmits_the_baseline(monkeypatch):
    from app.newsline.notices import clear_skip_notice, peek_skip_notice
    from app.schedule_logic import resolve_slot
    from app.slots import slot_from_parts

    monkeypatch.setattr(
        "app.newsline.playlist.newsline_unavailable_reason",
        lambda state=None: "Newsline is not available",
    )
    radio = StubRadio()
    played: list[str] = []
    lock = __import__("threading").Lock()
    station = Announcement(name="Station ID", text="id")
    news = Announcement(
        name="News",
        kind="newsline",
        station_id_announcement_id=station.id,
        text="",
    )
    tech = Announcement(name="Tech Net Pre-Announcement", text="net")
    overlay = Schedule(
        name="Newsline Test",
        kind="weekly",
        announcement_id=news.id,
        days=["fri"],
        slots=["18:00"],
    )
    baseline = Schedule(name="Baseline ID", kind="baseline", announcement_id=tech.id)
    document = StoreDocument(
        announcements=[station, news, tech],
        schedules=[baseline, overlay],
    )
    slot = slot_from_parts(NOW.date(), "18:00", ZONE)
    assert slot is not None

    def load():
        again = resolve_slot(document, slot)
        assert again.schedule is not None
        return LoadedFire(
            announcement=again.announcement,
            schedule=again.schedule,
            silence=False,
            skipped_detail=again.skipped_detail,
        )

    deps = _deps(
        radio,
        synthesize=lambda _item: _pcm(),
        play=lambda _chunks: played.append("play"),
        sleep=lambda _seconds: None,
        defer=lambda _when, _ctx: None,
        lock=lock,
    )
    deps.load = load
    ctx = FireContext(
        announcement_id=news.id,
        schedule_id=overlay.id,
        slot_key=slot.key,
        fired_at=NOW,
        deadline=NOW + timedelta(minutes=5),
        trigger="scheduled",
    )
    clear_skip_notice()
    try:
        assert handle_fire(ctx, deps) == "transmitted"
        assert played == ["play"]
        notice = peek_skip_notice()
        assert notice is not None
        assert "Newsline is not available" in str(notice["detail"])
        assert "Tech Net Pre-Announcement will play instead" in str(notice["detail"])
    finally:
        clear_skip_notice()


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
        trigger="scheduled",
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
