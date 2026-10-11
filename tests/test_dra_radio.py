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


def test_transmit_uses_usb_lead_on_dra():
    events: list[tuple[str, object]] = []

    class FakeDra:
        def set_ptt(self, keyed: bool) -> None:
            events.append(("ptt", keyed))

        def play_transmit_audio(self, chunks, *, lead_seconds: float) -> None:
            events.append(("lead", lead_seconds))

        def channel_busy(self) -> bool:
            return False

    slept: list[float] = []
    transmit(
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=FakeDra(),  # type: ignore[arg-type]
        sleep_fn=slept.append,
        lead_seconds=0.6,
    )
    assert slept == []
    assert ("lead", 0.6) in events
    assert events[0] == ("ptt", True)
    assert events[-1] == ("ptt", False)


def test_create_radio_stub_default(monkeypatch):
    monkeypatch.setenv("TTS_RADIO_BACKEND", "stub")
    set_radio(None)
    radio = create_radio_from_config()
    from app.radio import StubRadio

    assert isinstance(radio, StubRadio)
