"""DRA-36M: USB stream (COMM OK) before PTT, then speech PCM with no duplicate lead."""

from __future__ import annotations

from app.audio import PcmChunk
from app.cm119 import Cm119HidAdapter
from app.dra_radio import DraRadio
from app.radio import transmit


def test_transmit_streams_usb_lead_before_ptt(monkeypatch):
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[3],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
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
        "lead:0.6",
        "ptt:True",
        "play:lead=0",
        "ptt:False",
    ]
