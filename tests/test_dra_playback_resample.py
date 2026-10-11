from __future__ import annotations

import numpy as np

from app.dra_playback import resample_pcm_int16_mono


def test_resample_pcm_int16_mono_doubles_rate():
    t = np.linspace(0, 1, 22050, endpoint=False)
    pcm = (16000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16).tobytes()
    out = resample_pcm_int16_mono(pcm, from_rate=22050, to_rate=44100)
    assert len(out) == 44100 * 2


def test_resample_pcm_int16_mono_noop():
    pcm = b"\x00\x01" * 100
    assert resample_pcm_int16_mono(pcm, from_rate=44100, to_rate=44100) is pcm
