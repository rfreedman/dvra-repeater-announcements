"""Scheduled/manual fires use on_ptt_keyed; USB prep runs on the radio itself."""

from __future__ import annotations

from collections.abc import Iterable

from app.audio import PcmChunk
from app.cm119 import Cm119HidAdapter
from app.dra_radio import DraRadio
from app.radio import transmit


import pytest
def test_transmit_prep_runs_with_on_ptt_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[4],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    prep_calls: list[object] = []
    keyed: list[bool] = []

    def fake_prepare() -> None:
        prep_calls.append(radio)

    def fake_set_ptt(value: bool) -> None:
        keyed.append(value)

    def fake_play(chunks: Iterable[PcmChunk], lead_seconds: float = 0) -> None:
        _ = chunks
        _ = lead_seconds

    def channel_clear(_radio: object, clear_seconds: float = 0) -> bool:
        _ = clear_seconds
        return False

    def on_ptt_keyed() -> None:
        keyed.append(True)

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
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=radio,
        clear_seconds=0,
        lead_seconds=0,
        on_ptt_keyed=on_ptt_keyed,
    )

    assert prep_calls == [radio]
    assert keyed[0] is True  # set_ptt(True)
    assert keyed[1] is True  # on_ptt_keyed
    assert keyed[-1] is False  # set_ptt(False)
