"""Speak/prepare map voice errors to 400, I/O to 502, and anything else to 500."""

from __future__ import annotations

import logging
import urllib.error

import pytest

from app.engines.base import VoiceError


def _patch(monkeypatch, route: str, fail) -> None:
    target = "prepare" if route == "prepare" else "stream_synthesize"
    monkeypatch.setattr(f"app.routes.speak.registry.{target}", fail)


def _post(client, route: str):
    if route == "prepare":
        return client.post("/api/prepare", json={"voice": "nope"})
    return client.post("/api/speak", json={"text": "hello"})


@pytest.mark.parametrize("route", ["prepare", "speak"])
def test_unknown_voice_is_400(client, monkeypatch, route):
    def fail(*_args, **_kwargs):
        raise VoiceError("Unknown Piper voice: nope")

    _patch(monkeypatch, route, fail)
    res = _post(client, route)
    assert res.status_code == 400
    assert res.json()["detail"] == "Unknown Piper voice: nope"


@pytest.mark.parametrize("route", ["prepare", "speak"])
def test_download_failure_is_502(client, monkeypatch, route):
    def fail(*_args, **_kwargs):
        raise VoiceError("Failed to download model") from urllib.error.URLError("timed out")

    _patch(monkeypatch, route, fail)
    res = _post(client, route)
    assert res.status_code == 502
    assert res.json()["detail"] == "Failed to download model"


@pytest.mark.parametrize("route", ["prepare", "speak"])
def test_oserror_is_502(client, monkeypatch, route):
    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    _patch(monkeypatch, route, fail)
    res = _post(client, route)
    assert res.status_code == 502
    assert "disk full" in res.json()["detail"]


@pytest.mark.parametrize("route", ["prepare", "speak"])
def test_unexpected_engine_error_is_500_and_logged(client, monkeypatch, caplog, route):
    def fail(*_args, **_kwargs):
        raise RuntimeError("onnx blew up")

    _patch(monkeypatch, route, fail)
    with caplog.at_level(logging.ERROR, logger="app.routes.speak"):
        res = _post(client, route)
    assert res.status_code == 500
    assert res.json()["detail"] == "Internal Server Error"
    assert "onnx blew up" not in res.text
    assert "Unexpected" in caplog.text
    assert "onnx blew up" in caplog.text
