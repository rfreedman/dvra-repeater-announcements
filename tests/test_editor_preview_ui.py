"""Editor Play/Speak locks the form; Newsline preview marks PTT with tones."""

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"


def test_form_locks_while_playing_and_stop_stays_on_playback_buttons() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "readOnly || editorPlaying" in source
    assert "readOnly || editorPlaying || !isEditorDirty()" in source
    assert "scheduleSave.disabled = readOnly || !isEditorDirty()" in source
    assert "document.getElementById(\"stop\").disabled = !playing" in source
    assert "stopNews.disabled = !playing" in source


def test_announcement_reset_undoes_edits_and_stays_disabled_when_clean() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    html = (APP_JS.parent / "index.html").read_text(encoding="utf-8")
    assert 'id="reset" disabled' in html
    assert 'id="newsline-reset" disabled' in html
    assert 'getElementById("reset").addEventListener("click", resetAnnouncementEdits)' in source
    assert (
        'getElementById("newsline-reset").addEventListener("click", resetAnnouncementEdits)'
        in source
    )
    helper = source.split("function resetAnnouncementEdits()", 1)[1].split("\nfunction ", 1)[0]
    assert "editorBaseline" in helper
    assert "nameEl.value = saved.name" in helper
    assert "textEl.value = saved.text" in helper
    assert "speedEl.value = saved.speed" in helper
    assert "pauseEl.value = saved.pause" in helper
    assert "renderVoices(saved.voice)" in helper
    assert "editorBaselinePrefs" in helper
    assert "editorBaselinePlaySeconds" in helper
    assert "catalog.default_voice" not in helper
    assert "catalog.speed" not in helper
    sync = source.split("function syncEditorSave()", 1)[1].split("function markEditorClean()", 1)[0]
    assert '["reset", "newsline-reset"]' in sync
    assert "readOnly || editorPlaying || !isEditorDirty()" in sync
    clean = source.split("function markEditorClean()", 1)[1].split("function clearEditorGuard()", 1)[0]
    assert "editorBaselinePrefs = localStorage.getItem(PREFS_KEY)" in clean
    assert "editorBaselinePlaySeconds = editorPlaySeconds" in clean


def test_save_stays_disabled_until_the_editor_differs_from_its_original() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    snapshot = source.split("function formSnapshot()", 1)[1].split("function isEditorDirty()", 1)[0]
    assert "announcement-kind" in snapshot
    assert "news-station" in snapshot
    assert 'payload.name = document.getElementById("sched-name").value' in snapshot
    sync = source.split("function syncEditorSave()", 1)[1].split("function markEditorClean()", 1)[0]
    assert "readOnly || editorPlaying || !isEditorDirty()" in sync
    assert "readOnly || !isEditorDirty()" in sync
    assert "syncEditorSave()" in source.split("function markEditorClean()", 1)[1].split(
        "function clearEditorGuard()", 1
    )[0]
    listeners = source.split('for (const id of ["view-announcement", "view-schedule"])', 1)[1].split(
        "async function saveNewslineSettings()", 1
    )[0]
    assert 'addEventListener("input", syncEditorSave)' in listeners
    assert 'addEventListener("change", syncEditorSave)' in listeners


def test_playback_stops_when_leaving_the_announcement_editor() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    helper = source.split("function stopEditorPlayback()", 1)[1].split("function stop()", 1)[0]
    assert 'startsWith("ann") && nextKey !== editorKey' in source
    assert 'addEventListener("pagehide"' in source
    assert "stopEditorPlayback()" in source.split("beforeunload")[1]
    assert "showKnownPlayLength()" in helper


def test_newsline_preview_uses_a_lower_tone_for_ptt_release() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    engage = _const_number(source, "PTT_ENGAGE_HZ")
    release = _const_number(source, "PTT_RELEASE_HZ")
    assert 700 <= release < engage <= 800
    assert _const_number(source, "PTT_TONE_SECONDS") >= 0.15
    assert "this.playTone(PTT_ENGAGE_HZ" in source
    assert "this.playTone(PTT_RELEASE_HZ" in source
    assert 'X-Preview-Framing") !== "name32+length32"' in source


def test_next_run_shows_paused_while_newsline_waits_for_clear_channel() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert 'runPhase === "channel_busy"' in source
    assert 'runPhase === "clear_window"' in source
    assert "Paused · channel busy" in source
    assert "Paused · clear hold" in source
    assert "Waiting to continue Newsline" in source


def test_next_run_shows_newsline_piece_progress() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "function newslineProgressNote(row)" in source
    assert "upcoming.piece_label" in source
    assert "news item ${upcoming.story_index} of ${upcoming.story_total}" in source
    assert "news item ${row.story_index} of ${row.story_total}" in source


def test_next_run_names_the_schedule_when_it_differs_from_the_announcement() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    html = (APP_JS.parent / "index.html").read_text(encoding="utf-8")
    css = (APP_JS.parent / "styles.css").read_text(encoding="utf-8")
    assert "function fillUpcomingIdentity(row)" in source
    assert "fillUpcomingIdentity(upcoming)" in source
    assert "fillUpcomingLine(" in source
    assert 'labelEl.className = "next-run-label"' in source
    assert 'valueEl.className = "next-run-value"' in source
    assert 'id="next-run-schedule-line"' in html
    assert 'id="next-run-announcement-line"' in html
    assert ".next-run-value" in css
    label_css = css.split(".next-run-label", 1)[1].split("}", 1)[0]
    value_css = css.split(".next-run-value", 1)[1].split("}", 1)[0]
    status_css = css.split(".next-run-status {", 1)[1].split("}", 1)[0]
    countdown_css = css.split(".next-run-countdown", 1)[1].split("}", 1)[0]
    assert "color: var(--copper)" in label_css and "color: var(--copper)" in status_css
    assert "font-size: 0.78rem" in label_css and "font-size: 0.78rem" in status_css
    assert "color: var(--ink)" in value_css and "color: var(--ink)" in countdown_css
    assert "font-size: 1.05rem" in value_css and "font-size: 1.05rem" in countdown_css
    assert "wght@400;500;600;700" in html
    assert "${item.name} (Newsline)" in source
    assert 'pick.value = "__pick__"' in source
    assert "Pick an announcement." in source


def test_announcement_text_limit_comes_from_voices_payload() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    html = (APP_JS.parent / "index.html").read_text(encoding="utf-8")
    textarea = html.split('id="text"', 1)[1].split(">", 1)[0]
    assert "maxlength" not in textarea
    assert "0 / 5000" not in html
    branding = source.split("function applyBrandingFromCatalog()", 1)[1].split(
        "async function loadVoices()", 1
    )[0]
    assert "catalog.max_text_chars" in branding
    assert "textEl.maxLength = maxChars" in branding


def test_new_forms_leave_name_days_and_slots_empty() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    html = (APP_JS.parent / "index.html").read_text(encoding="utf-8")
    assert 'id="name" type="text" maxlength="120" />' in html
    assert 'id="sched-name" type="text" maxlength="120" />' in html
    assert 'placeholder="Station ID"' not in html
    assert 'placeholder="Weekly"' not in html
    templates = source.split("const TEMPLATES = {", 1)[1].split("};", 1)[0]
    assert "name:" not in templates
    assert "days:" not in templates
    assert "slots:" not in templates
    assert "event_slot:" not in templates
    filler = source.split("function fillScheduleForm(schedule)", 1)[1].split(
        "function renderClock", 1
    )[0]
    assert '|| ["sun"]' not in filler
    assert '|| "wed"' not in filler
    assert '|| "19:00"' not in filler
    assert '["21:00"]' not in filler
    assert "Choose a slot…" in source
    assert "fillEventSlotSelect(schedule?.event_slot)" in filler
    assert "fillSlotChips(schedule?.slots || [])" in filler
    assert "announcements[0]" not in filler
    select_fn = source.split("function fillAnnouncementSelect", 1)[1].split(
        "function isCountdownKind", 1
    )[0]
    assert "announcements[0]" not in select_fn


def test_newsline_home_status_shows_processing_step_and_elapsed() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "function newslineElapsedSuffix(seconds)" in source
    assert "newsline.step" in source
    assert "newsline.elapsed_seconds" in source
    assert "`${head}${step}${elapsed}`" in source


def test_newsline_check_button_disables_while_processing_or_playing() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "function syncNewslineCheckButton()" in source
    assert "Unavailable while Newsline is on the air" in source
    assert "A Newsline check is already running" in source
    assert "newslinePlaying || Boolean(lastNewsline?.playing)" in source
    click = source.split('getElementById("newsline-check").addEventListener("click"', 1)[1]
    assert "if (button?.disabled) return;" in click.split("});", 1)[0]


def test_today_clock_highlights_the_next_transmission_slot() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    css = (APP_JS.parent / "styles.css").read_text(encoding="utf-8")
    marker = source.split("function markUpcomingClockCell()", 1)[1].split("function nextRunSortKey", 1)[0]
    assert "stationDateStamp" in marker
    assert "settings.timezone" in source.split("function stationDateStamp", 1)[1].split(
        "function markUpcomingClockCell", 1
    )[0]
    assert 'upcoming?.status !== "waiting"' in marker
    assert "fireDay !== preview" in marker
    assert 'aria-current", "true"' in marker
    assert "cell.dataset.slot = row.slot" in source.split("function renderClock", 1)[1].split(
        "function stationDateStamp", 1
    )[0]
    tick = source.split("function tickUpcoming()", 1)[1].split("function shortCalendarLabel", 1)[0]
    assert tick.index("markUpcomingClockCell()") < tick.index("if (homeSseConnected) return;")
    assert ".clock-cell.clock-upcoming" in css
    assert "outline: 2px solid var(--copper)" in css


def test_running_now_shows_elapsed_clock() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "function elapsedSecondsSince(iso)" in source
    assert 'played == null ? "Now" : `${formatSegmentElapsed(played)}`' in source


def test_newsline_status_shows_segment_name_and_elapsed_clock() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "function streamingPieceStatus(name, elapsedSeconds)" in source
    assert "Streaming… ${segmentDisplayName(name)} ${formatSegmentElapsed(elapsedSeconds)}" in source
    assert "this._startSegmentClock(label, signal)" in source
    assert "setInterval(tick, 1000)" in source
    assert 'replace(/\\.mp3$/i, "")' in source


def test_speak_status_shows_elapsed_and_total() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "function playingStatus(elapsedSeconds, totalSeconds)" in source
    assert (
        "Playing… ${formatSegmentElapsed(elapsedSeconds)} / ${formatSegmentElapsed(totalSeconds)}"
        in source
    )
    assert 'res.headers.get("X-Play-Seconds")' in source
    assert "this._startPlayClock(totalSeconds, signal)" in source
    assert "knownTotal = Number.isFinite(totalSeconds) && totalSeconds > 0 ? totalSeconds : 0" in source


def test_saved_announcement_shows_idle_length() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "function idlePlayStatus(seconds)" in source
    assert "Length ${formatSegmentElapsed(seconds)}" in source
    assert "function showKnownPlayLength()" in source
    assert "editorPlaySeconds =" in source.split("async function loadAnnouncement", 1)[1]
    assert "clearKnownPlayLength()" in source
    assert "formatSegmentElapsed(item.play_seconds)" in source.split("function renderLibrary", 1)[1]


def _const_number(source: str, name: str) -> float:
    marker = f"const {name} = "
    start = source.index(marker) + len(marker)
    end = source.index(";", start)
    return float(source[start:end].strip())
