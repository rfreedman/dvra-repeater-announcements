"""DRA-36M: PTT, then USB lead silence, then speech PCM with no duplicate lead."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

import numpy as np
import pytest

from app.audio import PcmChunk
from app.cm119 import Cm119HidAdapter
from app.dra_playback import play_dra_lead_silence
from app.dra_radio import DraRadio
from app.radio import transmit


def _dra_radio() -> DraRadio:
    return DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[3],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )


def test_transmit_keys_ptt_then_usb_lead_in_same_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lead silence is prepended in play_transmit_audio so COMM OK never drops mid-TX."""
    radio = _dra_radio()
    order: list[str] = []

    def fake_prepare() -> None:
        order.append("prepare")

    def fake_set_ptt(keyed: bool) -> None:
        order.append(f"ptt:{keyed}")

    def fake_play(chunks: Iterable[PcmChunk], lead_seconds: float = 0) -> None:
        _ = chunks
        order.append(f"play:lead={lead_seconds}")

    def channel_clear(_radio: object, clear_seconds: float = 0) -> bool:
        _ = clear_seconds
        return False

    monkeypatch.setattr(
        radio,
        "prepare_for_transmit",
        fake_prepare,
    )
    monkeypatch.setattr(radio, "set_ptt", fake_set_ptt)
    monkeypatch.setattr(
        radio,
        "play_transmit_audio",
        fake_play,
    )
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        channel_clear,
    )

    transmit(
        [PcmChunk(pcm_int16=b"\x00\x10" * 100, sample_rate=22050)],
        radio=radio,
        lead_seconds=0.6,
        clear_seconds=0,
    )

    assert order == [
        "prepare",
        "ptt:True",
        "play:lead=0.6",
        "ptt:False",
    ]


def test_transmit_keys_ptt_when_lead_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    radio = _dra_radio()
    order: list[str] = []

    def fake_prepare() -> None:
        pass

    def fake_set_ptt(keyed: bool) -> None:
        order.append(f"ptt:{keyed}")

    def fake_play(chunks: Iterable[PcmChunk], lead_seconds: float = 0) -> None:
        _ = chunks
        order.append(f"play:lead={lead_seconds}")

    def channel_clear(_radio: object, clear_seconds: float = 0) -> bool:
        _ = clear_seconds
        return False

    monkeypatch.setattr(radio, "prepare_for_transmit", fake_prepare)
    monkeypatch.setattr(radio, "set_ptt", fake_set_ptt)
    monkeypatch.setattr(
        radio,
        "play_transmit_audio",
        fake_play,
    )
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        channel_clear,
    )

    transmit(
        [PcmChunk(pcm_int16=b"\x00\x10" * 100, sample_rate=22050)],
        radio=radio,
        lead_seconds=0,
        clear_seconds=0,
    )

    assert order == ["ptt:True", "play:lead=0", "ptt:False"]


def test_transmit_unkeys_if_usb_playback_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    radio = _dra_radio()
    order: list[str] = []

    def fake_prepare() -> None:
        pass

    def _raise_play(
        chunks: Iterable[PcmChunk], lead_seconds: float = 0
    ) -> None:
        _ = chunks
        order.append(f"play:lead={lead_seconds}")
        raise RuntimeError("playback failed")

    def fake_set_ptt(keyed: bool) -> None:
        order.append(f"ptt:{keyed}")

    def channel_clear(_radio: object, clear_seconds: float = 0) -> bool:
        _ = clear_seconds
        return False

    monkeypatch.setattr(radio, "prepare_for_transmit", fake_prepare)
    monkeypatch.setattr(radio, "set_ptt", fake_set_ptt)
    monkeypatch.setattr(radio, "play_transmit_audio", _raise_play)
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        channel_clear,
    )

    with pytest.raises(RuntimeError, match="playback failed"):
        transmit(
            [PcmChunk(pcm_int16=b"\x00\x10" * 100, sample_rate=22050)],
            radio=radio,
            lead_seconds=0.6,
            clear_seconds=0,
        )

    assert order == ["ptt:True", "play:lead=0.6", "ptt:False"]


def test_dual_lead_silence_starts_both_devices_together(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sequential lead drops COMM OK on radio 1 before radio 2 starts (staggered RF)."""
    events: list[tuple[str, int, float]] = []
    barrier = threading.Barrier(2)

    def fake_play(
        mono: np.ndarray, *, device_index: int, **_kwargs: object
    ) -> None:
        _ = mono
        events.append(("start", device_index, time.monotonic()))
        _ = barrier.wait(timeout=1.0)
        time.sleep(0.05)
        events.append(("end", device_index, time.monotonic()))

    monkeypatch.setattr("app.dra_playback.play_mono_f32_to_dra_device", fake_play)
    def fake_samplerate(_index: int) -> int:
        return 48000

    monkeypatch.setattr("app.dra_playback.dra_device_samplerate", fake_samplerate)

    play_dra_lead_silence(
        device_indices=[3, 5],
        channel="left",
        lead_seconds=0.1,
    )

    starts = [e for e in events if e[0] == "start"]
    ends = [e for e in events if e[0] == "end"]
    assert {device for _kind, device, _t in starts} == {3, 5}
    assert {device for _kind, device, _t in ends} == {3, 5}
    assert max(t for _kind, _device, t in starts) < min(t for _kind, _device, t in ends)


def test_dual_lead_silence_raises_first_device_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_play(
        mono: np.ndarray, *, device_index: int, **_kwargs: object
    ) -> None:
        _ = mono
        if device_index == 5:
            raise RuntimeError("lead failed on second DRA")
        time.sleep(0.05)

    monkeypatch.setattr("app.dra_playback.play_mono_f32_to_dra_device", fake_play)
    def fake_samplerate(_index: int) -> int:
        return 48000

    monkeypatch.setattr("app.dra_playback.dra_device_samplerate", fake_samplerate)

    with pytest.raises(RuntimeError, match="lead failed on second DRA"):
        play_dra_lead_silence(
            device_indices=[3, 5],
            channel="left",
            lead_seconds=0.1,
        )
