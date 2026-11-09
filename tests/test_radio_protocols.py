"""Tests for the layered radio capability Protocols and as_* helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from app.audio import PcmChunk
import pytest
from app.radio import (
    AudioReplugRadio,
    ClearWaitRadio,
    CosPollingRadio,
    CosSnapshotRadio,
    IdleCreditRadio,
    StubRadio,
    UsbAbortRadio,
    UsbTransmitRadio,
    as_audio_replug,
    as_clear_wait,
    as_cos_polling,
    as_cos_snapshot,
    as_idle_credit,
    as_usb_abort,
    as_usb_transmit,
    transmit,
)


class _MinimalRadio:
    """Bare-minimum Radio — only required methods, no optional capabilities."""

    def channel_busy(self) -> bool:
        return False

    def set_ptt(self, keyed: bool) -> None:
        _ = keyed
        pass


class _IdleCreditFake(_MinimalRadio):
    """Fake that adds only continuous_idle_seconds."""

    def continuous_idle_seconds(self) -> float:
        return 5.0


class _FullDraLike(_MinimalRadio):
    """Fake implementing every optional Protocol (like DraRadio)."""

    def continuous_idle_seconds(self) -> float:
        return 1.0

    def wait_for_transmit_clear(self, *, clear_seconds: float, poll_seconds: float, deadline: datetime, now: Callable[[], datetime], sleep_fn: Callable[[float], None], fail_fast_on_busy: bool = False) -> bool:
        _ = clear_seconds
        _ = poll_seconds
        _ = deadline
        _ = now
        _ = sleep_fn
        _ = fail_fast_on_busy
        return True

    def cos_defer_snapshot(self) -> tuple[bool | None, float]:
        return False, 1.0

    def prepare_for_transmit(self) -> None:
        pass

    def begin_usb_transmit(self) -> None:
        pass

    def end_usb_transmit(self) -> None:
        pass

    def play_transmit_audio(self, chunks: Iterable[PcmChunk], *, lead_seconds: float) -> None:
        _ = chunks
        _ = lead_seconds
        pass

    @property
    def usb_hid_present(self) -> bool:
        return True

    def transmit_abort_is_set(self) -> bool:
        return False

    def warm_open_hid(self) -> None:
        pass

    def refresh_cos(self) -> bool:
        return False

    @property
    def ptt_keyed(self) -> bool:
        return False

    def sync_audio_output_indices_if_changed(self, *, after_usb_event: bool = False) -> bool:
        _ = after_usb_event
        return False


def test_stub_matches_cos_snapshot() -> None:
    stub = StubRadio()
    assert as_cos_snapshot(stub) is stub


def test_stub_does_not_match_idle_credit() -> None:
    assert as_idle_credit(StubRadio()) is None


def test_stub_does_not_match_clear_wait() -> None:
    assert as_clear_wait(StubRadio()) is None


def test_stub_does_not_match_usb_transmit() -> None:
    assert as_usb_transmit(StubRadio()) is None


def test_stub_does_not_match_usb_abort() -> None:
    assert as_usb_abort(StubRadio()) is None


def test_stub_does_not_match_cos_polling() -> None:
    assert as_cos_polling(StubRadio()) is None


def test_stub_does_not_match_audio_replug() -> None:
    assert as_audio_replug(StubRadio()) is None


def test_minimal_matches_no_optional() -> None:
    radio = _MinimalRadio()
    assert as_idle_credit(radio) is None
    assert as_clear_wait(radio) is None
    assert as_cos_snapshot(radio) is None
    assert as_usb_transmit(radio) is None
    assert as_usb_abort(radio) is None
    assert as_cos_polling(radio) is None
    assert as_audio_replug(radio) is None


def test_idle_credit_fake_matches_only_idle() -> None:
    radio = _IdleCreditFake()
    idle = as_idle_credit(radio)
    assert idle is radio
    assert idle is not None
    assert idle.continuous_idle_seconds() == 5.0
    assert as_clear_wait(radio) is None
    assert as_cos_snapshot(radio) is None


def test_full_dra_like_matches_all() -> None:
    radio = _FullDraLike()
    assert as_idle_credit(radio) is radio
    assert as_clear_wait(radio) is radio
    assert as_cos_snapshot(radio) is radio
    assert as_usb_transmit(radio) is radio
    assert as_usb_abort(radio) is radio
    assert as_cos_polling(radio) is radio
    assert as_audio_replug(radio) is radio


def test_on_ptt_keyed_runs_when_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    radio = _MinimalRadio()
    events: list[str] = []

    def play(_chunks: Iterable[PcmChunk]) -> None:
        events.append("play")

    def channel_clear(_radio: object, clear_seconds: float = 0) -> bool:
        _ = clear_seconds
        return False

    def sleep(_seconds: float) -> None:
        pass

    def on_ptt_keyed() -> None:
        events.append("keyed")

    monkeypatch.setattr(
        "app.radio.channel_blocks_transmit",
        channel_clear,
    )
    transmit(
        [PcmChunk(pcm_int16=b"\x00\x00", sample_rate=22050)],
        radio=radio,
        play_fn=play,
        sleep_fn=sleep,
        lead_seconds=0,
        on_ptt_keyed=on_ptt_keyed,
    )
    assert events == ["keyed", "play"]


def test_isinstance_checks() -> None:
    assert isinstance(_IdleCreditFake(), IdleCreditRadio)
    assert not isinstance(_MinimalRadio(), IdleCreditRadio)
    assert isinstance(_FullDraLike(), ClearWaitRadio)
    assert isinstance(_FullDraLike(), CosPollingRadio)
    assert isinstance(_FullDraLike(), UsbTransmitRadio)
    assert isinstance(_FullDraLike(), UsbAbortRadio)
    assert isinstance(_FullDraLike(), AudioReplugRadio)
    assert isinstance(StubRadio(), CosSnapshotRadio)
    assert not isinstance(StubRadio(), IdleCreditRadio)
