from __future__ import annotations

from app.audio import PcmChunk
from app.dra_radio import DraRadio
from app.radio import create_radio_from_config, set_radio, transmit


class _FakeAdapter:
    def __init__(self, busy: bool | None):
        self.busy = busy

    def read_cos_busy(self, _hid, *, line, active_low, persistent_dev=None):
        return self.busy


def test_dra_channel_busy_or_across_adapters():
    radio = DraRadio(
        hid_adapters=[_FakeAdapter(False), _FakeAdapter(False)],  # type: ignore[arg-type]
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio.channel_busy() is False

    radio = DraRadio(
        hid_adapters=[_FakeAdapter(False), _FakeAdapter(True)],  # type: ignore[arg-type]
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    assert radio.channel_busy() is True


def test_transmit_uses_usb_lead_on_dra(monkeypatch):
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
    monkeypatch.setattr(
        "app.dra_alsa_mixer.sync_cm119_speaker_unmute",
        lambda _r: None,
    )
    monkeypatch.setattr(
        "app.dra_alsa_mixer.prepare_dra_for_transmit",
        lambda _r: None,
    )
    monkeypatch.setattr(
        "app.dra_playback.play_dra_lead_silence",
        lambda **kwargs: events.append(("usb_lead", kwargs["lead_seconds"])),
    )
    monkeypatch.setattr(radio, "set_ptt", lambda keyed: events.append(("ptt", keyed)))
    monkeypatch.setattr(
        radio,
        "play_transmit_audio",
        lambda _chunks, lead_seconds=0: events.append(("play_lead", lead_seconds)),
    )
    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        lambda _radio, clear_seconds=0: False,
    )

    transmit(
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=radio,
        lead_seconds=0.6,
        clear_seconds=0,
    )
    assert events == [
        ("ptt", True),
        ("usb_lead", 0.6),
        ("play_lead", 0),
        ("ptt", False),
    ]


def test_create_radio_stub_default(monkeypatch):
    monkeypatch.setenv("TTS_RADIO_BACKEND", "stub")
    set_radio(None)
    radio = create_radio_from_config()
    from app.radio import StubRadio

    assert isinstance(radio, StubRadio)
