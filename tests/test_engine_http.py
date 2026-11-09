"""Speak/prepare map voice errors to 400, I/O to 502, and anything else to 500."""

from __future__ import annotations

import logging
import urllib.error
from collections.abc import Callable
from typing import Literal, NoReturn, Protocol, cast

import pytest

from app.tts.base import VoiceError
from tests.jsonutil import json_dict


from fastapi.testclient import TestClient

RouteName = Literal["prepare", "speak"]


class _Response(Protocol):
    status_code: int
    text: str

    def json(self) -> object: ...


def _patch(monkeypatch: pytest.MonkeyPatch, route: RouteName, fail: Callable[..., NoReturn]) -> None:
    target = "prepare" if route == "prepare" else "synthesize"
    monkeypatch.setattr(f"app.routes.speak.registry.{target}", fail)


def _post(client: TestClient, route: RouteName) -> _Response:
    if route == "prepare":
        return cast(
            _Response,
            cast(object, client.post("/api/prepare", json={"voice": "nope"})),
        )
    return cast(
        _Response,
        cast(object, client.post("/api/speak", json={"text": "hello"})),
    )


@pytest.mark.parametrize("route", ["prepare", "speak"])
def test_unknown_voice_is_400(client: TestClient, monkeypatch: pytest.MonkeyPatch, route: RouteName) -> None:
    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise VoiceError("Unknown Piper voice: nope")

    _patch(monkeypatch, route, fail)
    res = _post(client, route)
    assert res.status_code == 400
    assert json_dict(res)["detail"] == "Unknown Piper voice: nope"


@pytest.mark.parametrize("route", ["prepare", "speak"])
def test_download_failure_is_502(client: TestClient, monkeypatch: pytest.MonkeyPatch, route: RouteName) -> None:
    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise VoiceError("Failed to download model") from urllib.error.URLError("timed out")

    _patch(monkeypatch, route, fail)
    res = _post(client, route)
    assert res.status_code == 502
    assert json_dict(res)["detail"] == "Failed to download model"


@pytest.mark.parametrize("route", ["prepare", "speak"])
def test_oserror_is_502(client: TestClient, monkeypatch: pytest.MonkeyPatch, route: RouteName) -> None:
    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise OSError("disk full")

    _patch(monkeypatch, route, fail)
    res = _post(client, route)
    assert res.status_code == 502
    assert "disk full" in str(json_dict(res)["detail"])


@pytest.mark.parametrize("route", ["prepare", "speak"])
def test_unexpected_engine_error_is_500_and_logged(client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, route: RouteName) -> None:
    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("onnx blew up")

    _patch(monkeypatch, route, fail)
    with caplog.at_level(logging.ERROR, logger="app.routes.speak"):
        res = _post(client, route)
    assert res.status_code == 500
    assert json_dict(res)["detail"] == "Internal Server Error"
    assert "onnx blew up" not in res.text
    assert "Unexpected" in caplog.text
    assert "onnx blew up" in caplog.text
