"""DRA-36M: PTT, then USB lead silence, then speech PCM with no duplicate lead."""

from __future__ import annotations

import threading
import time

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


def test_transmit_keys_ptt_then_usb_lead_in_same_stream(monkeypatch):
    """Lead silence is prepended in play_transmit_audio so COMM OK never drops mid-TX."""
    radio = _dra_radio()
    order: list[str] = []

    monkeypatch.setattr(
        radio,
        "prepare_for_transmit",
        lambda: order.append("prepare"),
    )
    monkeypatch.setattr(radio, "set_ptt", lambda keyed: order.append(f"ptt:{keyed}"))
    monkeypatch.setattr(
        radio,
        "play_transmit_audio",
        lambda chunks, lead_seconds=0: order.append(f"play:lead={lead_seconds}"),
    )
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        lambda _radio, clear_seconds=0: False,
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


def test_transmit_keys_ptt_when_lead_is_zero(monkeypatch):
    radio = _dra_radio()
    order: list[str] = []

    monkeypatch.setattr(radio, "prepare_for_transmit", lambda: None)
    monkeypatch.setattr(radio, "set_ptt", lambda keyed: order.append(f"ptt:{keyed}"))
    monkeypatch.setattr(
        radio,
        "play_transmit_audio",
        lambda chunks, lead_seconds=0: order.append(f"play:lead={lead_seconds}"),
    )
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        lambda _radio, clear_seconds=0: False,
    )

    transmit(
        [PcmChunk(pcm_int16=b"\x00\x10" * 100, sample_rate=22050)],
        radio=radio,
        lead_seconds=0,
        clear_seconds=0,
    )

    assert order == ["ptt:True", "play:lead=0", "ptt:False"]


def test_transmit_unkeys_if_usb_playback_raises(monkeypatch):
    radio = _dra_radio()
    order: list[str] = []

    monkeypatch.setattr(radio, "prepare_for_transmit", lambda: None)

    def _raise_play(chunks, lead_seconds=0):
        order.append(f"play:lead={lead_seconds}")
        raise RuntimeError("playback failed")

    monkeypatch.setattr(radio, "set_ptt", lambda keyed: order.append(f"ptt:{keyed}"))
    monkeypatch.setattr(radio, "play_transmit_audio", _raise_play)
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        lambda _radio, clear_seconds=0: False,
    )

    with pytest.raises(RuntimeError, match="playback failed"):
        transmit(
            [PcmChunk(pcm_int16=b"\x00\x10" * 100, sample_rate=22050)],
            radio=radio,
            lead_seconds=0.6,
            clear_seconds=0,
        )

    assert order == ["ptt:True", "play:lead=0.6", "ptt:False"]


def test_dual_lead_silence_starts_both_devices_together(monkeypatch):
    """Sequential lead drops COMM OK on radio 1 before radio 2 starts (staggered RF)."""
    events: list[tuple[str, int, float]] = []
    barrier = threading.Barrier(2)

    def fake_play(mono, *, device_index, **_kwargs):
        events.append(("start", device_index, time.monotonic()))
        barrier.wait(timeout=1.0)
        time.sleep(0.05)
        events.append(("end", device_index, time.monotonic()))

    monkeypatch.setattr("app.dra_playback.play_mono_f32_to_dra_device", fake_play)
    monkeypatch.setattr("app.dra_playback.dra_device_samplerate", lambda _i: 48000)

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


def test_dual_lead_silence_raises_first_device_error(monkeypatch):
    def fake_play(mono, *, device_index, **_kwargs):
        if device_index == 5:
            raise RuntimeError("lead failed on second DRA")
        time.sleep(0.05)

    monkeypatch.setattr("app.dra_playback.play_mono_f32_to_dra_device", fake_play)
    monkeypatch.setattr("app.dra_playback.dra_device_samplerate", lambda _i: 48000)

    with pytest.raises(RuntimeError, match="lead failed on second DRA"):
        play_dra_lead_silence(
            device_indices=[3, 5],
            channel="left",
            lead_seconds=0.1,
        )
