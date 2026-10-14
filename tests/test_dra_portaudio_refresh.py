"""PortAudio device list refresh after Linux USB replug."""

from __future__ import annotations

from app.dra_playback import refresh_portaudio_device_list


def test_refresh_portaudio_noop_off_linux(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("app.dra_playback.sys.platform", "darwin")

    class FakeSd:
        @staticmethod
        def _terminate() -> None:
            calls.append("term")

        @staticmethod
        def _initialize() -> None:
            calls.append("init")

    monkeypatch.setattr("sounddevice._terminate", FakeSd._terminate, raising=False)
    refresh_portaudio_device_list()
    assert calls == []


def test_refresh_portaudio_reinits_on_linux(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("app.dra_playback.sys.platform", "linux")

    def term() -> None:
        calls.append("term")

    def init() -> None:
        calls.append("init")

    monkeypatch.setattr("sounddevice._terminate", term)
    monkeypatch.setattr("sounddevice._initialize", init)
    refresh_portaudio_device_list()
    assert calls == ["term", "init"]
