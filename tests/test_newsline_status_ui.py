"""Home Newsline status shows the distribution date and last check time."""

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


def test_release_friday_is_a_calendar_date() -> None:
    source = _function_source("formatReleaseFriday")
    assert "new Date(iso" not in source
    assert "new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))" in source


def test_idle_status_includes_distribution_date_and_last_check() -> None:
    facts = _function_source("newslineFacts")
    assert "`Distributed ${distributed}.`" in facts
    assert "`Last checked ${formatWhen(newsline.last_check_at)}.`" in facts
    render = _function_source("renderNewsline")
    first_facts = render.index("newslineFacts(newsline)")
    for marker in (
        "Downloading Newsline report",
        "Processing Newsline report",
        "Checking for a new Newsline report",
    ):
        assert render.index(marker) < first_facts
    assert "`Last check failed: ${stopped}${facts}`" in render
    assert "is ready to play.${facts}" in render
    assert "`No report downloaded yet.${facts}`" in render
