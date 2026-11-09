from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from app.audio import PcmChunk
from app.cm119 import Cm119HidAdapter, HidDevice, HidModule
from app.dra_radio import DraRadio
from app.radio import create_radio_from_config, set_radio, transmit


import pytest
class _FakeAdapter:
    busy: bool | None

    def __init__(self, busy: bool | None) -> None:
        self.busy = busy

    def read_cos_busy(
        self,
        _hid: HidModule,
        *,
        line: str,
        active_low: bool,
        persistent_dev: HidDevice | None = None,
    ) -> bool | None:
        _ = line
        _ = active_low
        _ = persistent_dev
        return self.busy


def test_dra_channel_busy_or_across_adapters() -> None:
    radio = DraRadio(
        hid_adapters=cast(
            list[Cm119HidAdapter],
            [_FakeAdapter(False), _FakeAdapter(False)],
        ),
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio.channel_busy() is False

    radio = DraRadio(
        hid_adapters=cast(
            list[Cm119HidAdapter],
            [_FakeAdapter(False), _FakeAdapter(True)],
        ),
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio.channel_busy() is True


def test_transmit_uses_usb_lead_on_dra(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.cm119 import Cm119HidAdapter
    from app.dra_radio import DraRadio

    events: list[tuple[str, object]] = []
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[3],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )

    def fake_prepare() -> None:
        pass

    def fake_set_ptt(keyed: bool) -> None:
        events.append(("ptt", keyed))

    def fake_play(_chunks: Iterable[PcmChunk], lead_seconds: float = 0) -> None:
        events.append(("play_lead", lead_seconds))

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
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=radio,
        lead_seconds=0.6,
        clear_seconds=0,
    )
    assert events == [
        ("ptt", True),
        ("play_lead", 0.6),
        ("ptt", False),
    ]


def test_create_radio_stub_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_RADIO_BACKEND", "stub")
    set_radio(None)
    radio = create_radio_from_config()
    from app.radio import StubRadio

    assert isinstance(radio, StubRadio)
