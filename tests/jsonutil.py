"""Typed reads of TestClient JSON bodies (``Response.json`` is ``Any``)."""

from __future__ import annotations

from typing import Protocol, cast


class _JsonBody(Protocol):
    def json(self) -> object: ...


def json_dict(response: _JsonBody) -> dict[str, object]:
    body: object = response.json()
    if not isinstance(body, dict):
        raise AssertionError(f"expected JSON object, got {type(body).__name__}")
    return cast(dict[str, object], body)


def json_list(response: _JsonBody) -> list[object]:
    body: object = response.json()
    if not isinstance(body, list):
        raise AssertionError(f"expected JSON list, got {type(body).__name__}")
    return cast(list[object], body)
