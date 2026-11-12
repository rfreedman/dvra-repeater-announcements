"""Status chip: Linux thermal label plus memory used and available."""

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


def test_linux_temp_uses_mac_label_and_rounded_celsius() -> None:
    source = _function_source("celsiusThermalText")
    assert "rounded < 55" in source
    assert 'label = "Nominal"' in source
    assert "rounded < 70" in source
    assert 'label = "Fair"' in source
    assert 'label = "Critical"' in source
    assert "`${label} (${rounded}°C)`" in source
    render = _function_source("renderSystemStats")
    assert "celsiusThermalText(celsius)" in render
    assert "tempText = thermalLabels[thermalState]" in render


def test_memory_slot_shows_used_and_available_after_percent() -> None:
    amount = _function_source("formatMemoryAmount")
    assert " GB" in amount
    assert " MB" in amount
    slot = _function_source("formatMemorySlot")
    assert "`${Math.round(Number(percent))}%`" in slot
    assert "`${used} used`" in slot
    assert "`${available} available`" in slot
    assert 'parts.join(" · ")' in slot
    render = _function_source("renderSystemStats")
    assert "formatMemorySlot(memoryPercent, memoryUsed, memoryAvailable)" in render
