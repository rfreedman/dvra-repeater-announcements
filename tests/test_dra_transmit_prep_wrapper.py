"""Scheduled/manual fires use on_ptt_keyed; USB prep runs on the radio itself."""

from __future__ import annotations

from app.audio import PcmChunk
from app.cm119 import Cm119HidAdapter
from app.dra_radio import DraRadio
from app.radio import transmit


def test_transmit_prep_runs_with_on_ptt_keyed(monkeypatch):
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[4],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    prep_calls: list[object] = []
    keyed: list[bool] = []

    monkeypatch.setattr(
        radio,
        "prepare_for_transmit",
        lambda: prep_calls.append(radio),
    )
    monkeypatch.setattr(radio, "set_ptt", lambda value: keyed.append(bool(value)))
    monkeypatch.setattr(
        radio,
        "play_transmit_audio",
        lambda chunks, lead_seconds=0: None,
    )
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        lambda _radio, clear_seconds=0: False,
    )

    transmit(
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=radio,
        clear_seconds=0,
        lead_seconds=0,
        on_ptt_keyed=lambda: keyed.append(True),
    )

    assert prep_calls == [radio]
    assert keyed[0] is True  # set_ptt(True)
    assert keyed[1] is True  # on_ptt_keyed
    assert keyed[-1] is False  # set_ptt(False)
