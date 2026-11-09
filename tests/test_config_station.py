"""Station identity env helpers."""

import pytest
from pytest import MonkeyPatch

from app import config as app_config


def test_require_env_rejects_blank(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("STATION_CALL", "   ")
    with pytest.raises(RuntimeError, match="STATION_CALL is required"):
        _ = app_config._require_env("STATION_CALL")  # pyright: ignore[reportPrivateUsage]


def test_require_env_returns_stripped(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("STATION_NAME", "  Example Club  ")
    assert app_config._require_env("STATION_NAME") == "Example Club"  # pyright: ignore[reportPrivateUsage]
