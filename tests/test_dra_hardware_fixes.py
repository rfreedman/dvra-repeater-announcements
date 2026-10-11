"""Dual-DRA: PTT unkeys on partial failure; both radios get audio at the same time."""
from __future__ import annotations

import numpy as np
import pytest

import app.dra_radio as dra_radio_mod
from app.cm119 import Cm119HidAdapter
from app.dra_playback import write_programs_interleaved
from app.dra_radio import DraRadio
from app.radio import ChannelBusyError


class _FakeDev:
    def __init__(self, name: str, fail_key: bool = False) -> None:
        self.name = name
        self.fail_key = fail_key
        self.keyed = False

    def write(self, data: bytes) -> int:
        keyed = bool(data[3] & 0x04)
        if keyed and self.fail_key:
            return -1
        self.keyed = keyed
        return len(data)

    def close(self) -> None:
        pass


def test_partial_ptt_failure_unkeys_radios_already_keyed(monkeypatch):
    devs = {b"a": _FakeDev("a"), b"b": _FakeDev("b", fail_key=True)}
    monkeypatch.setattr(dra_radio_mod, "ensure_hid_module", lambda: object())
    monkeypatch.setattr(dra_radio_mod, "open_cm119_hid_at", lambda _hid, path: devs[path])
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"a", "one"), Cm119HidAdapter(b"b", "two")],
        audio_indices=[0, 1],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    with pytest.raises(ChannelBusyError):
        radio.set_ptt(True)
    assert devs[b"a"].keyed is False


class _FakeStream:
    def __init__(self, name: str, samplerate: int, log: list[tuple[str, int]]) -> None:
        self.name = name
        self.samplerate = samplerate
        self._log = log

    def write_mono_f32(self, mono: np.ndarray) -> None:
        self._log.append((self.name, len(mono)))


def test_two_radios_are_fed_in_alternating_short_blocks():
    log: list[tuple[str, int]] = []
    streams = [_FakeStream("r1", 44100, log), _FakeStream("r2", 48000, log)]
    programs = [np.zeros(44100, dtype=np.float32), np.zeros(48000, dtype=np.float32)]
    write_programs_interleaved(streams, programs, block_seconds=0.05)  # type: ignore[arg-type]
    names = [name for name, _n in log]
    assert names[:4] == ["r1", "r2", "r1", "r2"]
    assert max(n for _name, n in log) <= 2400
    assert sum(n for name, n in log if name == "r1") == 44100
    assert sum(n for name, n in log if name == "r2") == 48000
