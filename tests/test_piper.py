from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.engines.piper import FEATURED_VOICES, PiperEngine


@dataclass
class _FakeChunk:
    audio_int16_bytes: bytes = b"\x00\x00"
    sample_rate: int = 22050
    sample_width: int = 2
    sample_channels: int = 1


class _FakeVoice:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.config = SimpleNamespace(sample_rate=22050)
        self.synthesize_calls: list[str] = []

    def synthesize(self, text: str, syn_config=None):
        self.synthesize_calls.append(text)
        yield _FakeChunk()


def _stub_piper(monkeypatch, voices_dir: Path, fail_ids: set[str] | None = None):
    loads: list[str] = []
    downloads: list[str] = []
    fail_ids = fail_ids or set()

    def fake_download(voice_id: str, target_dir: Path) -> None:
        downloads.append(voice_id)
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        (Path(target_dir) / f"{voice_id}.onnx").write_bytes(b"onnx")

    def fake_load(path):
        voice_id = Path(path).stem
        if voice_id in fail_ids:
            raise RuntimeError(f"boom {voice_id}")
        loads.append(voice_id)
        return _FakeVoice(Path(path))

    monkeypatch.setattr("app.engines.piper.download_voice", fake_download)
    monkeypatch.setattr("app.engines.piper.PiperVoice.load", fake_load)
    return loads, downloads


def test_preload_loads_featured_and_extra_voices_once(tmp_path: Path, monkeypatch):
    (tmp_path / "custom-local.onnx").write_bytes(b"onnx")
    (tmp_path / "en_US-lessac-high.onnx").write_bytes(b"onnx")
    loads, downloads = _stub_piper(monkeypatch, tmp_path)
    engine = PiperEngine(voices_dir=tmp_path)

    loaded = engine.preload()
    featured = [meta.id for meta in FEATURED_VOICES]
    assert loaded == featured + ["custom-local"]
    assert "en_US-lessac-high" not in loaded
    assert loads == featured + ["custom-local"]
    assert set(downloads) == set(featured)

    engine.prepare("en_US-lessac-medium")
    engine.prepare("amy")
    engine.prepare("custom-local")
    assert loads.count("en_US-lessac-medium") == 1
    assert loads.count("en_US-amy-medium") == 1
    assert loads.count("custom-local") == 1
    assert engine._models["en_US-lessac-medium"] is engine._load("en_US-lessac-medium")


def test_preload_continues_when_one_voice_fails(tmp_path: Path, monkeypatch):
    loads, _downloads = _stub_piper(monkeypatch, tmp_path, fail_ids={"en_US-amy-medium"})
    engine = PiperEngine(voices_dir=tmp_path)

    loaded = engine.preload()
    featured = [meta.id for meta in FEATURED_VOICES]
    assert "en_US-amy-medium" not in loaded
    assert set(loaded) == set(featured) - {"en_US-amy-medium"}
    assert "en_US-lessac-medium" in loads
    assert "en_US-ryan-medium" in loads


def test_lifespan_preloads_before_scheduler(monkeypatch):
    from fastapi.testclient import TestClient

    from app.server import app

    order: list[str] = []
    monkeypatch.setattr(
        "app.registry.EngineRegistry.preload",
        lambda self: order.append("preload") or [],
    )
    monkeypatch.setattr("app.server.start_scheduler", lambda: order.append("start"))
    monkeypatch.setattr("app.server.stop_scheduler", lambda: None)

    with TestClient(app):
        pass

    assert order[:2] == ["preload", "start"]
