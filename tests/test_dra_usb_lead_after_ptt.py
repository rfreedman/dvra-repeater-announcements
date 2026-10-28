"""DRA-36M: PTT, then USB lead silence, then speech PCM with no duplicate lead."""

from __future__ import annotations

import pytest

from app.audio import PcmChunk
from app.cm119 import Cm119HidAdapter
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


def test_transmit_keys_ptt_then_usb_lead(monkeypatch):
    radio = _dra_radio()
    order: list[str] = []

    monkeypatch.setattr(
        "app.dra_alsa_mixer.sync_cm119_speaker_unmute",
        lambda _r: order.append("unmute"),
    )
    monkeypatch.setattr(
        "app.dra_alsa_mixer.prepare_dra_for_transmit",
        lambda _r: order.append("prepare"),
    )
    monkeypatch.setattr(
        "app.dra_playback.play_dra_lead_silence",
        lambda **kwargs: order.append(f"lead:{kwargs.get('lead_seconds')}"),
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
        "unmute",
        "prepare",
        "ptt:True",
        "lead:0.6",
        "play:lead=0",
        "ptt:False",
    ]


def test_transmit_keys_ptt_when_lead_is_zero(monkeypatch):
    radio = _dra_radio()
    order: list[str] = []

    monkeypatch.setattr("app.dra_alsa_mixer.sync_cm119_speaker_unmute", lambda _r: None)
    monkeypatch.setattr("app.dra_alsa_mixer.prepare_dra_for_transmit", lambda _r: None)
    monkeypatch.setattr(
        "app.dra_playback.play_dra_lead_silence",
        lambda **kwargs: order.append("lead"),
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
        lead_seconds=0,
        clear_seconds=0,
    )

    assert order == ["ptt:True", "play:lead=0", "ptt:False"]


def test_transmit_unkeys_if_usb_lead_raises(monkeypatch):
    radio = _dra_radio()
    order: list[str] = []

    monkeypatch.setattr("app.dra_alsa_mixer.sync_cm119_speaker_unmute", lambda _r: None)
    monkeypatch.setattr("app.dra_alsa_mixer.prepare_dra_for_transmit", lambda _r: None)

    def _raise_lead(**kwargs):
        order.append("lead")
        raise RuntimeError("lead failed")

    monkeypatch.setattr("app.dra_playback.play_dra_lead_silence", _raise_lead)
    monkeypatch.setattr(radio, "set_ptt", lambda keyed: order.append(f"ptt:{keyed}"))
    monkeypatch.setattr(
        radio,
        "play_transmit_audio",
        lambda chunks, lead_seconds=0: order.append("play"),
    )
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        lambda _radio, clear_seconds=0: False,
    )

    with pytest.raises(RuntimeError, match="lead failed"):
        transmit(
            [PcmChunk(pcm_int16=b"\x00\x10" * 100, sample_rate=22050)],
            radio=radio,
            lead_seconds=0.6,
            clear_seconds=0,
        )

    assert order == ["ptt:True", "lead", "ptt:False"]
