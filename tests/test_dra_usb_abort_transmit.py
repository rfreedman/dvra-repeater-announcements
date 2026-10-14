"""Abort in-flight transmit when CM119 USB is unplugged."""

from __future__ import annotations

import numpy as np
import pytest

from app.cm119 import Cm119HidAdapter
from app.dra_playback import play_pcm_chunks_to_dra_devices
from app.dra_radio import DraRadio
from app.radio import TransmitAbortError


def test_playback_abort_raises_transmit_abort_error():
    abort_after = {"blocks": 2}

    def should_abort() -> bool:
        abort_after["blocks"] -= 1
        return abort_after["blocks"] <= 0

    class FakeStream:
        samplerate = 44100

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            pass

        def write_mono_f32(self, mono: np.ndarray) -> None:
            pass

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            "app.dra_playback.DraUsbPlayback",
            lambda *args, **kwargs: FakeStream(),
        )
        monkeypatch.setattr("app.dra_playback.dra_device_samplerate", lambda _i: 44100)
        from app.audio import PcmChunk

        chunks = [
            PcmChunk(pcm_int16=b"\x00\x00" * 44100, sample_rate=22050),
        ]
        with pytest.raises(TransmitAbortError):
            play_pcm_chunks_to_dra_devices(
                chunks,
                device_indices=[0],
                should_abort=should_abort,
            )
    finally:
        monkeypatch.undo()


def test_aplay_playback_honors_should_abort(monkeypatch):
    monkeypatch.setattr("app.dra_playback._linux_use_aplay", lambda: True)
    monkeypatch.setattr("app.dra_playback.dra_device_samplerate", lambda _i: 48000)
    monkeypatch.setattr(
        "app.dra_playback._alsa_playback_devices_for_portaudio_index",
        lambda _i: ["hw:1,0"],
    )
    monkeypatch.setattr(
        "app.dra_alsa_mixer.sync_cm119_speaker_unmute_for_indices",
        lambda _indices: None,
    )
    monkeypatch.setattr(
        "app.dra_pulse_mixer.sync_pulse_sink_unmute_for_portaudio_index",
        lambda _index: None,
    )

    class FakeProc:
        def __init__(self) -> None:
            self.stdin = type("W", (), {"write": lambda *_a, **_k: None, "close": lambda: None})()
            self.stderr = None
            self._done = False

        def poll(self):
            return 0 if self._done else None

        def kill(self) -> None:
            self._done = True

        def wait(self, timeout=None):
            self._done = True

    proc = FakeProc()
    monkeypatch.setattr(
        "app.dra_playback.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )

    abort = {"flag": False}

    def should_abort() -> bool:
        abort["flag"] = True
        return True

    from app.audio import PcmChunk

    chunks = [PcmChunk(pcm_int16=b"\x00\x00" * 100, sample_rate=22050)]
    with pytest.raises(TransmitAbortError):
        play_pcm_chunks_to_dra_devices(
            chunks,
            device_indices=[1],
            should_abort=should_abort,
        )


def test_usb_absent_during_usb_transmit_lead_signals_abort(monkeypatch):
    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    radio.begin_usb_transmit()
    monkeypatch.setattr(radio, "_enumerate_hid_entries", lambda: [])
    radio.note_usb_hid_presence()
    assert radio._transmit_abort.is_set()


def test_usb_absent_during_ptt_signals_abort(monkeypatch):
    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    with radio._lock:
        radio._ptt_keyed = True
    monkeypatch.setattr(radio, "_enumerate_hid_entries", lambda: [])
    radio.note_usb_hid_presence()
    assert radio.ptt_keyed is False
    assert radio._transmit_abort.is_set()
