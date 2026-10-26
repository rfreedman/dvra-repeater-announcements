"""The home stream's keepalive comment must not freeze the page.

A comment frame such as ``: ping`` parses as no event. The reader has to
advance past it. Skipping that advance and looping again spins the tab.
"""

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


def test_keepalive_frame_advances_before_the_reader_loops():
    function = _function_source("takeSseEvents")
    loop = function[function.index("while"):]
    advance = loop.index('splitAt = buffer.indexOf("\\n\\n")')
    assert "continue" not in loop[:advance]
    assert "streamHomeLiveEvents" not in function
    home = _function_source("streamHomeLiveEvents")
    stats = _function_source("streamSystemStatsEvents")
    assert "takeSseEvents" in home
    assert "takeSseEvents" in stats
    assert "if (!parsed) continue" not in home
    assert "if (!parsed) continue" not in stats
