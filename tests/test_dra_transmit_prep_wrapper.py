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
    sync_calls: list[object] = []
    prep_calls: list[object] = []

    monkeypatch.setattr(
        "app.dra_alsa_mixer.sync_cm119_speaker_unmute",
        lambda r: sync_calls.append(r),
    )
    monkeypatch.setattr(
        "app.dra_alsa_mixer.prepare_dra_for_transmit",
        lambda r: prep_calls.append(r),
    )
    monkeypatch.setattr(wrapped, "set_ptt", lambda keyed: None)
    monkeypatch.setattr(
        wrapped,
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

    assert sync_calls == [wrapped]
    assert prep_calls == [wrapped]
    assert sync_calls[0]._inner is radio  # type: ignore[attr-defined]
