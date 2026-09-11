from __future__ import annotations

from pathlib import Path

import pytest

from app.radio import StubRadio, set_radio
from app.store import AnnouncementStore, set_store


@pytest.fixture(autouse=True)
def no_voice_preload(monkeypatch):
    monkeypatch.setattr("app.registry.EngineRegistry.preload", lambda self: [])


@pytest.fixture
def store(tmp_path: Path):
    path = tmp_path / "announcements.json"
    item = AnnouncementStore(path)
    set_store(item)
    yield item
    set_store(None)


@pytest.fixture
def radio():
    item = StubRadio()
    set_radio(item)
    yield item
    set_radio(None)
