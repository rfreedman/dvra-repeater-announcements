"""Scheduled/manual fires wrap the radio for running UI hooks."""

from __future__ import annotations

from app.cm119 import Cm119HidAdapter
from app.dra_radio import DraRadio
from app.fire import _RunningOnKeyRadio
from app.radio import transmit


def test_transmit_prep_runs_on_running_on_key_wrapper(monkeypatch):
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[4],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    wrapped = _RunningOnKeyRadio(radio, on_key=None)
    prep_calls: list[object] = []

    monkeypatch.setattr(
        radio,
        "prepare_for_transmit",
        lambda: prep_calls.append(radio),
    )
    monkeypatch.setattr(wrapped, "set_ptt", lambda keyed: None)
    monkeypatch.setattr(
        radio,
        "play_transmit_audio",
        lambda chunks, lead_seconds=0: None,
    )
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        lambda _radio, clear_seconds=0: False,
    )

    from app.audio import PcmChunk

    transmit(
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=wrapped,
        clear_seconds=0,
        lead_seconds=0,
    )

    assert prep_calls == [radio]
