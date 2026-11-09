from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from piper import SynthesisConfig

from app.models import Announcement
from app.registry import EngineRegistry
from app.tts.piper import (
    PiperEngine,
    PiperVoiceMeta,
    featured_voices,
    load_featured_voices,
    set_featured_voices,
)


@dataclass
class _FakeChunk:
    audio_int16_bytes: bytes = b"\x00\x00"
    sample_rate: int = 22050
    sample_width: int = 2
    sample_channels: int = 1


class _FakeVoice:
    path: Path
    config: SimpleNamespace

    def __init__(self, path: Path) -> None:
        self.path = path
        self.config = SimpleNamespace(sample_rate=22050)
        self.synthesize_calls: list[str] = []

    def synthesize(
        self, text: str, syn_config: SynthesisConfig | None = None
    ) -> Iterator[_FakeChunk]:
        _ = syn_config
        self.synthesize_calls.append(text)
        yield _FakeChunk()


@pytest.fixture
def featured(tmp_path: Path) -> Iterator[tuple[PiperVoiceMeta, ...]]:
    path = tmp_path / "piper-voices.json"
    _ = path.write_text(
        json.dumps(
            {
                "voices": [
                    {
                        "id": "en_US-lessac-medium",
                        "alias": "lessac",
                        "name": "Lessac",
                        "gender": "female",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "Clear American English.",
                    },
                    {
                        "id": "en_US-amy-medium",
                        "alias": "amy",
                        "name": "Amy",
                        "gender": "female",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "Warm American English female voice.",
                    },
                    {
                        "id": "en_US-ryan-medium",
                        "alias": "ryan",
                        "name": "Ryan",
                        "gender": "male",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "American English male voice.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    voices = load_featured_voices(path)
    previous = featured_voices
    set_featured_voices(voices)
    yield voices
    set_featured_voices(previous)


def _stub_piper(
    monkeypatch: pytest.MonkeyPatch,
    voices_dir: Path,
    fail_ids: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    _ = voices_dir
    loads: list[str] = []
    downloads: list[str] = []
    fail_ids = fail_ids or set()

    def fake_download(voice_id: str, target_dir: Path) -> None:
        downloads.append(voice_id)
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        _ = (Path(target_dir) / f"{voice_id}.onnx").write_bytes(b"onnx")

    def fake_load(path: str | Path) -> _FakeVoice:
        voice_id = Path(path).stem
        if voice_id in fail_ids:
            raise RuntimeError(f"boom {voice_id}")
        loads.append(voice_id)
        return _FakeVoice(Path(path))

    monkeypatch.setattr("app.tts.piper.download_voice", fake_download)
    monkeypatch.setattr("app.tts.piper.PiperVoice.load", fake_load)
    return loads, downloads


def test_load_featured_voices_from_repo_file() -> None:
    assert len(featured_voices) >= 1
    assert featured_voices[0].id
    assert featured_voices[0].alias


def test_load_featured_voices_rejects_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    _ = path.write_text(
        json.dumps(
            {
                "voices": [
                    {
                        "id": "en_US-amy-medium",
                        "alias": "amy",
                        "name": "Amy",
                        "gender": "female",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "one",
                    },
                    {
                        "id": "en_US-amy-medium",
                        "alias": "amy2",
                        "name": "Amy2",
                        "gender": "female",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "two",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate voice id"):
        _ = load_featured_voices(path)


def test_load_featured_voices_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        _ = load_featured_voices(tmp_path / "missing.json")


def test_preload_loads_featured_and_local_voices_including_high(featured: tuple[PiperVoiceMeta, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def quality_allowed(_voice_id: str) -> bool:
        return True

    monkeypatch.setattr("app.tts.piper.piper_quality_allowed", quality_allowed)
    _ = (tmp_path / "custom-local.onnx").write_bytes(b"onnx")
    _ = (tmp_path / "en_US-lessac-high.onnx").write_bytes(b"onnx")
    loads, downloads = _stub_piper(monkeypatch, tmp_path)
    engine = PiperEngine(voices_dir=tmp_path)

    loaded = engine.preload()
    featured_ids = [meta.id for meta in featured]
    assert loaded == featured_ids + ["custom-local", "en_US-lessac-high"]
    assert "en_US-lessac-high" in loaded
    assert loads == featured_ids + ["custom-local", "en_US-lessac-high"]
    assert set(downloads) == set(featured_ids)

    listed = {item.id: item for item in engine.list_voices()}
    assert listed["en_US-lessac-high"].quality == "high"
    assert listed["custom-local"].quality == "custom"

    _ = engine.prepare("en_US-lessac-medium")
    _ = engine.prepare("amy")
    _ = engine.prepare("custom-local")
    _ = engine.prepare("en_US-lessac-high")
    assert loads.count("en_US-lessac-medium") == 1
    assert loads.count("en_US-amy-medium") == 1
    assert loads.count("custom-local") == 1
    assert loads.count("en_US-lessac-high") == 1
    assert engine.cached_voice("en_US-lessac-medium") is engine.load_voice("en_US-lessac-medium")


def test_medium_qualities_excludes_high_from_list_and_preload(featured: tuple[PiperVoiceMeta, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def quality_allowed(voice_id: str) -> bool:
        return voice_id.endswith("-medium")

    monkeypatch.setattr(
        "app.tts.piper.piper_quality_allowed",
        quality_allowed,
    )
    _ = (tmp_path / "custom-local.onnx").write_bytes(b"onnx")
    _ = (tmp_path / "en_US-lessac-high.onnx").write_bytes(b"onnx")
    loads, _downloads = _stub_piper(monkeypatch, tmp_path)
    engine = PiperEngine(voices_dir=tmp_path)

    loaded = engine.preload()
    featured_ids = [meta.id for meta in featured]
    assert loaded == featured_ids
    assert "en_US-lessac-high" not in loaded
    assert "custom-local" not in loaded
    assert loads == featured_ids

    listed_ids = {item.id for item in engine.list_voices()}
    assert "en_US-lessac-high" not in listed_ids
    assert "custom-local" not in listed_ids
    assert "en_US-lessac-medium" in listed_ids


def test_preload_continues_when_one_voice_fails(featured: tuple[PiperVoiceMeta, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def quality_allowed(_voice_id: str) -> bool:
        return True

    monkeypatch.setattr("app.tts.piper.piper_quality_allowed", quality_allowed)
    loads, _downloads = _stub_piper(monkeypatch, tmp_path, fail_ids={"en_US-amy-medium"})
    engine = PiperEngine(voices_dir=tmp_path)

    loaded = engine.preload()
    featured_ids = [meta.id for meta in featured]
    assert "en_US-amy-medium" not in loaded
    assert set(loaded) == set(featured_ids) - {"en_US-amy-medium"}
    assert "en_US-lessac-medium" in loads
    assert "en_US-ryan-medium" in loads


def test_load_featured_voices_rejects_config_url_without_onnx_url(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    _ = path.write_text(
        json.dumps(
            {
                "voices": [
                    {
                        "id": "custom",
                        "alias": "custom",
                        "name": "Custom",
                        "gender": "male",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "x",
                        "config_url": "https://example.com/custom.onnx.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="config_url requires onnx_url"):
        _ = load_featured_voices(path)


def test_load_featured_voices_rejects_bad_url(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    _ = path.write_text(
        json.dumps(
            {
                "voices": [
                    {
                        "id": "custom",
                        "alias": "custom",
                        "name": "Custom",
                        "gender": "male",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "x",
                        "onnx_url": "ftp://example.com/x.onnx",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="http\\(s\\) URL"):
        _ = load_featured_voices(path)


def test_custom_url_download_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "voices.json"
    _ = path.write_text(
        json.dumps(
            {
                "voices": [
                    {
                        "id": "norman",
                        "alias": "norman",
                        "name": "Norman",
                        "gender": "male",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "Norm!",
                        "onnx_url": "https://example.com/norman.onnx",
                        "config_url": "https://example.com/norman.onnx.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    voices = load_featured_voices(path)
    previous = featured_voices
    set_featured_voices(voices)
    try:
        fetched: list[tuple[str, str]] = []

        def fake_download(url: str, dest: Path) -> None:
            fetched.append((url, dest.name))
            _ = dest.write_bytes(b"onnx-bytes" if dest.name.endswith(".onnx") else b"{}")

        def fake_load(path: str | Path) -> _FakeVoice:
            return _FakeVoice(Path(path))

        def quality_allowed(_voice_id: str) -> bool:
            return True

        monkeypatch.setattr("app.tts.piper._download_url_to_file", fake_download)
        monkeypatch.setattr("app.tts.piper.PiperVoice.load", fake_load)
        monkeypatch.setattr("app.tts.piper.piper_quality_allowed", quality_allowed)

        engine = PiperEngine(voices_dir=tmp_path / "piper")
        _ = engine.prepare("norman")
        assert (tmp_path / "piper" / "norman.onnx").exists()
        assert {url for url, _name in fetched} == {
            "https://example.com/norman.onnx",
            "https://example.com/norman.onnx.json",
        }
        # Second prepare should not re-download
        fetched.clear()
        _ = engine.prepare("norman")
        assert fetched == []
    finally:
        set_featured_voices(previous)


def test_non_pattern_id_without_url_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "voices.json"
    _ = path.write_text(
        json.dumps(
            {
                "voices": [
                    {
                        "id": "norman",
                        "alias": "norman",
                        "name": "Norman",
                        "gender": "male",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "Norm!",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    voices = load_featured_voices(path)
    previous = featured_voices
    set_featured_voices(voices)
    try:
        def fake_load(path: str | Path) -> _FakeVoice:
            return _FakeVoice(Path(path))

        monkeypatch.setattr("app.tts.piper.PiperVoice.load", fake_load)
        engine = PiperEngine(voices_dir=tmp_path / "piper")
        with pytest.raises(Exception, match="no download source"):
            _ = engine.prepare("norman")
    finally:
        set_featured_voices(previous)


def test_hf_download_when_urls_omitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "voices.json"
    _ = path.write_text(
        json.dumps(
            {
                "voices": [
                    {
                        "id": "en_US-lessac-medium",
                        "alias": "lessac",
                        "name": "Lessac",
                        "gender": "female",
                        "locale": "en_US",
                        "quality": "medium",
                        "description": "Clear American English.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    voices = load_featured_voices(path)
    previous = featured_voices
    set_featured_voices(voices)
    try:
        custom_fetches: list[str] = []
        hf_downloads: list[str] = []

        def fake_url_download(url: str, dest: Path) -> None:
            custom_fetches.append(url)
            _ = dest.write_bytes(b"x")

        def fake_hf(voice_id: str, target_dir: Path) -> None:
            hf_downloads.append(voice_id)
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            _ = (Path(target_dir) / f"{voice_id}.onnx").write_bytes(b"onnx")

        def fake_load(path: str | Path) -> _FakeVoice:
            return _FakeVoice(Path(path))

        def quality_allowed(_voice_id: str) -> bool:
            return True

        monkeypatch.setattr("app.tts.piper._download_url_to_file", fake_url_download)
        monkeypatch.setattr("app.tts.piper.download_voice", fake_hf)
        monkeypatch.setattr("app.tts.piper.PiperVoice.load", fake_load)
        monkeypatch.setattr("app.tts.piper.piper_quality_allowed", quality_allowed)

        engine = PiperEngine(voices_dir=tmp_path / "piper")
        _ = engine.prepare("en_US-lessac-medium")
        assert hf_downloads == ["en_US-lessac-medium"]
        assert custom_fetches == []
    finally:
        set_featured_voices(previous)


def test_lifespan_preloads_before_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.server import app

    order: list[str] = []

    def preload(_self: EngineRegistry) -> list[str]:
        order.append("preload")
        return []

    def warm(_announcements: list[Announcement] | None = None) -> None:
        order.append("warm")

    def radio() -> None:
        order.append("radio")

    def start() -> None:
        order.append("start")

    def stop() -> None:
        return

    monkeypatch.setattr(
        "app.registry.EngineRegistry.preload",
        preload,
    )
    monkeypatch.setattr("app.server.warm_all", warm)
    monkeypatch.setattr("app.server.init_radio_at_startup", radio)
    monkeypatch.setattr("app.server.start_scheduler", start)
    monkeypatch.setattr("app.server.stop_scheduler", stop)

    with TestClient(app):
        pass

    assert order[:4] == ["radio", "preload", "warm", "start"]
