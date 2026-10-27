"""Whisper gets PCM arrays, not file paths (faster-whisper 1.2.1 vs PyAV 19)."""

from __future__ import annotations

import wave
from types import SimpleNamespace

import numpy as np

from app.newsline.engine.transcribe import _words_from_model, pcm_float32_from_wav


def _write_mono16_wav(path, *, samples: bytes, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples)


def test_pcm_float32_from_wav_scales_int16(tmp_path):
    wav = tmp_path / "tone.wav"
    _write_mono16_wav(wav, samples=np.array([0, 16384, -32768], dtype="<i2").tobytes())
    pcm = pcm_float32_from_wav(wav)
    assert pcm.dtype == np.float32
    assert pcm.tolist() == [0.0, 0.5, -1.0]


def test_words_from_model_passes_ndarray_not_path(tmp_path):
    wav = tmp_path / "clip.wav"
    _write_mono16_wav(wav, samples=b"\x00\x00" * 160)

    class FakeWord:
        word = "hello"
        start = 0.0
        end = 0.1

    class FakeSeg:
        words = [FakeWord()]
        text = "hello"
        start = 0.0
        end = 0.1

    received: dict[str, object] = {}

    def transcribe(audio, **_kwargs):
        received["audio"] = audio
        return [FakeSeg()], None

    words = _words_from_model(SimpleNamespace(transcribe=transcribe), wav)
    audio = received["audio"]
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert len(audio) == 160
    assert [w.word for w in words] == ["hello"]
