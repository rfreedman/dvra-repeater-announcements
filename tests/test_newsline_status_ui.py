"""Home Newsline status shows the air date, the feed publication date, and the last check time."""

from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"


def _function_source(name: str) -> str:
    source = APP_JS.read_text(encoding="utf-8")
    marker = f"function {name}("
    start = source.index(marker)
    open_brace = source.index("{", start)
    depth = 0
    quote = ""
    escape = False
    for index in range(open_brace, len(source)):
        char = source[index]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Could not find the end of {name}")


def test_home_schedule_line_uses_the_loaded_settings() -> None:
    source = _function_source("newslineScheduleText")
    assert "Checks for new release Thursday ${start}" in source
    assert "settings.news_check_start" in source
    assert "settings.news_check_stop" in source
    assert "settings.news_check_interval_hours" in source
    assert "settings.repeater_timeout_seconds" in source
    assert 'return `Thursday' not in source


def test_published_time_uses_the_station_clock_in_the_string() -> None:
    source = _function_source("formatPublished")
    assert "new Date(iso" not in source
    assert "formatReleaseFriday" in source
    assert "${match[4]}:${match[5]}" in source


def test_release_friday_is_a_calendar_date() -> None:
    source = _function_source("formatReleaseFriday")
    assert "new Date(iso" not in source
    assert "new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))" in source


def test_idle_status_includes_distribution_date_and_last_check() -> None:
    facts = _function_source("newslineFacts")
    assert "`Published ${published}.`" in facts
    assert "`Air date ${airDate}.`" in facts
    assert facts.index("`Published ${published}.`") < facts.index("`Air date ${airDate}.`")
    assert "newsline.published_on" in facts
    assert "${lines[lines.length - 1]} ${sentence}" in facts
    assert "`Last checked ${formatWhen(newsline.last_check_at)}.`" in facts
    assert 'lines.join("\\n")' in facts
    render = _function_source("renderNewsline")
    first_facts = render.index("newslineFacts(")
    for marker in (
        "Downloading Newsline report",
        "Processing Newsline report",
        "Checking for a new Newsline report",
    ):
        assert render.index(marker) < first_facts
    assert "newslineFacts([`Last check failed: ${stopped}`], newsline)" in render
    assert "`Report ${newsline.report_number} ready.`" in render
    assert "is ready to play" not in render
    assert 'newslineFacts(["No report downloaded yet."], newsline)' in render
