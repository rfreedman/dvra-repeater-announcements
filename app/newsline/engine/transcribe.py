from __future__ import annotations

import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.newsline.engine.models import WordTiming

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

# Full-file ASR is only needed for short slices; longer ones get head+tail
# (actionable bleed / opener checks only look at the ends).
_FULL_ASR_MAX_SEC = 45.0
_HEAD_ASR_SEC = 18.0
_TAIL_ASR_SEC = 25.0


def _decode_to_wav(
    audio_path: Path,
    *,
    start: float | None = None,
    duration: float | None = None,
) -> Path:
    """Decode input (optionally a window) to 16 kHz mono WAV via ffmpeg."""
    fd, name = tempfile.mkstemp(suffix=".wav", prefix="arnewsline_")
    os.close(fd)
    out = Path(name)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if start is not None and start > 0.001:
        cmd.extend(["-ss", f"{start:.3f}"])
    cmd.extend(["-i", str(audio_path)])
    if duration is not None:
        cmd.extend(["-t", f"{max(0.1, duration):.3f}"])
    cmd.extend(["-ar", "16000", "-ac", "1", str(out)])
    subprocess.run(cmd, check=True)
    return out


def pcm_float32_from_wav(wav_path: Path) -> np.ndarray:
    """16 kHz mono 16-bit WAV as float32 in [-1, 1] for Whisper (skip PyAV decode)."""
    with wave.open(str(wav_path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(
                f"expected mono 16-bit WAV, got {w.getnchannels()}ch "
                f"{w.getsampwidth() * 8}-bit"
            )
        if w.getframerate() != 16000:
            raise ValueError(f"expected 16 kHz WAV, got {w.getframerate()} Hz")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _words_from_model(
    model: WhisperModel, wav_path: Path, *, time_offset: float = 0.0
) -> list[WordTiming]:
    segments, _info = model.transcribe(
        pcm_float32_from_wav(wav_path),
        word_timestamps=True,
        language="en",
        vad_filter=True,
    )
    words: list[WordTiming] = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                if w.word and w.start is not None and w.end is not None:
                    words.append(
                        WordTiming(
                            word=w.word.strip(),
                            start=time_offset + float(w.start),
                            end=time_offset + float(w.end),
                        )
                    )
        elif segment.text.strip() and segment.start is not None and segment.end is not None:
            words.append(
                WordTiming(
                    word=segment.text.strip(),
                    start=time_offset + float(segment.start),
                    end=time_offset + float(segment.end),
                )
            )
    return words


def probe_audio_duration(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def transcribe_slice_for_qa(
    audio_path: Path,
    *,
    model_name: str = "small.en",
    full_max_sec: float = _FULL_ASR_MAX_SEC,
    head_sec: float = _HEAD_ASR_SEC,
    tail_sec: float = _TAIL_ASR_SEC,
    force_full: bool = False,
    model_slot: int = 0,
) -> tuple[list[WordTiming], bool]:
    """
    ASR an exported slice for QA.

    Short files are transcribed in full. Longer files only get the head and
    tail (enough for bleed / opener / sign-off checks). Returns
    ``(words, partial)`` where ``partial`` is True when mid-slice audio was
    skipped (soft full-coverage checks should be omitted).

    ``force_full`` is used for INTRO slots (may include billboard / first story).
    ``model_slot`` selects which cached Whisper instance to use (parallel QA).
    """
    from app.newsline.engine.whisper_model import get_whisper_model

    duration = probe_audio_duration(audio_path)
    model = get_whisper_model(model_name, slot=model_slot)

    if force_full or duration <= full_max_sec + 0.05:
        wav_path = _decode_to_wav(audio_path)
        try:
            return _words_from_model(model, wav_path), False
        finally:
            wav_path.unlink(missing_ok=True)

    # Head + tail only; leave a gap so word times stay on the slice timeline.
    head_dur = min(head_sec, duration)
    tail_start = max(head_dur, duration - tail_sec)
    words: list[WordTiming] = []

    head_wav = _decode_to_wav(audio_path, start=0.0, duration=head_dur)
    try:
        words.extend(_words_from_model(model, head_wav, time_offset=0.0))
    finally:
        head_wav.unlink(missing_ok=True)

    if tail_start < duration - 0.05:
        tail_wav = _decode_to_wav(
            audio_path, start=tail_start, duration=duration - tail_start
        )
        try:
            words.extend(_words_from_model(model, tail_wav, time_offset=tail_start))
        finally:
            tail_wav.unlink(missing_ok=True)

    return words, True


def _save_qa_cache(path: Path, words: list[WordTiming], *, partial: bool) -> None:
    payload = {
        "partial": partial,
        "words": [{"word": w.word, "start": w.start, "end": w.end} for w in words],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_qa_cache(path: Path) -> tuple[list[WordTiming], bool] | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        # Legacy full-slice cache — treat as complete transcript.
        words = [
            WordTiming(word=item["word"], start=item["start"], end=item["end"])
            for item in payload
        ]
        return words, False
    if not isinstance(payload, dict) or "words" not in payload:
        return None
    words = [
        WordTiming(word=item["word"], start=item["start"], end=item["end"])
        for item in payload["words"]
    ]
    return words, bool(payload.get("partial", False))
