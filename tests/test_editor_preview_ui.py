"""Editor Play/Speak locks the form; Newsline preview marks PTT with tones."""

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"


def test_form_locks_while_playing_and_stop_stays_on_playback_buttons():
    source = APP_JS.read_text(encoding="utf-8")
    assert "readOnly || editorPlaying" in source
    assert "save.disabled = lockAnnouncement" in source
    assert "document.getElementById(\"stop\").disabled = !playing" in source
    assert "stopNews.disabled = !playing" in source


def test_playback_stops_when_leaving_the_announcement_editor():
    source = APP_JS.read_text(encoding="utf-8")
    helper = source.split("function stopEditorPlayback()", 1)[1].split("function stop()", 1)[0]
    assert 'startsWith("ann") && nextKey !== editorKey' in source
    assert 'addEventListener("pagehide"' in source
    assert "stopEditorPlayback()" in source.split("beforeunload")[1]
    assert 'setStatus("Stopped")' in helper


def test_newsline_preview_uses_a_lower_tone_for_ptt_release():
    source = APP_JS.read_text(encoding="utf-8")
    engage = _const_number(source, "PTT_ENGAGE_HZ")
    release = _const_number(source, "PTT_RELEASE_HZ")
    assert 700 <= release < engage <= 800
    assert _const_number(source, "PTT_TONE_SECONDS") >= 0.15
    assert "this.playTone(PTT_ENGAGE_HZ" in source
    assert "this.playTone(PTT_RELEASE_HZ" in source
    assert 'X-Preview-Framing") !== "name32+length32"' in source


def test_next_run_names_the_schedule_when_it_differs_from_the_announcement():
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


def test_newsline_status_shows_segment_name_and_elapsed_clock():
    source = APP_JS.read_text(encoding="utf-8")
    assert "function streamingPieceStatus(name, elapsedSeconds)" in source
    assert "Streaming… ${segmentDisplayName(name)} ${formatSegmentElapsed(elapsedSeconds)}" in source
    assert "this._startSegmentClock(label, signal)" in source
    assert "setInterval(tick, 1000)" in source
    assert 'replace(/\\.mp3$/i, "")' in source


def _const_number(source: str, name: str) -> float:
    marker = f"const {name} = "
    start = source.index(marker) + len(marker)
    end = source.index(";", start)
    return float(source[start:end].strip())
