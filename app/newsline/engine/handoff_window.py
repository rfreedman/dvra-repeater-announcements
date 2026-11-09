"""Transcribe a short handoff window and return master-timeline words."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from app.newsline.engine.models import WordTiming
from app.newsline.engine.transcribe import pcm_float32_from_wav


def _decode_window_wav(audio: Path, t0: float, t1: float, out_wav: Path) -> None:
    dur = max(0.1, t1 - t0)
    _ = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, t0):.3f}",
            "-i",
            str(audio),
            "-t",
            f"{dur:.3f}",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(out_wav),
        ],
        check=True,
    )


def transcribe_handoff_window(
    audio: Path,
    t0: float,
    t1: float,
    *,
    model_name: str = "base.en",
) -> list[WordTiming]:
    """ASR the handoff neighborhood; word times are absolute on the master timeline."""
    from app.newsline.engine.whisper_model import get_whisper_model

    model = get_whisper_model(model_name)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "win.wav"
        _decode_window_wav(audio, t0, t1, wav)
        segments, _info = model.transcribe(
            pcm_float32_from_wav(wav),
            word_timestamps=True,
            language="en",
            vad_filter=False,
        )
        words: list[WordTiming] = []
        for seg in segments:
            seg_words = cast(object, getattr(seg, "words", None))
            if not seg_words:
                continue
            for w in cast(Iterable[object], seg_words):
                word = cast(object, getattr(w, "word", None))
                start = cast(object, getattr(w, "start", None))
                end = cast(object, getattr(w, "end", None))
                if (
                    not isinstance(word, str)
                    or not word
                    or not isinstance(start, (int, float))
                    or not isinstance(end, (int, float))
                ):
                    continue
                words.append(
                    WordTiming(
                        word=word.strip(),
                        start=t0 + float(start),
                        end=t0 + float(end),
                    )
                )
        return words
