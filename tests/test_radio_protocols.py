"""Tests for the layered radio capability Protocols and as_* helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from app.audio import PcmChunk
from app.fire_types import RunningOnKeyRadio
from app.radio import (
    AudioReplugRadio,
    ClearWaitRadio,
    CosPollingRadio,
    CosSnapshotRadio,
    IdleCreditRadio,
    Radio,
    StubRadio,
    UsbPlayRadio,
    as_audio_replug,
    as_clear_wait,
    as_cos_polling,
    as_cos_snapshot,
    as_idle_credit,
    as_usb_play,
)


class _MinimalRadio:
    """Bare-minimum Radio — only required methods, no optional capabilities."""

    def channel_busy(self) -> bool:
        return False

    def set_ptt(self, keyed: bool) -> None:
        pass


class _IdleCreditFake(_MinimalRadio):
    """Fake that adds only continuous_idle_seconds."""

    def continuous_idle_seconds(self) -> float:
        return 5.0


class _FullDraLike(_MinimalRadio):
    """Fake implementing every optional Protocol (like DraRadio)."""

    def continuous_idle_seconds(self) -> float:
        return 1.0

    def wait_for_transmit_clear(
        self,
        *,
        clear_seconds: float,
        poll_seconds: float,
        deadline: datetime,
        now: Callable[[], datetime],
        sleep_fn: Callable[[float], None],
        fail_fast_on_busy: bool = False,
    ) -> bool:
        return True

    def cos_defer_snapshot(self) -> tuple[bool | None, float]:
        return False, 1.0

    def play_transmit_audio(
        self,
        chunks: Iterable[PcmChunk],
        *,
        lead_seconds: float,
    ) -> None:
        pass

    def warm_open_hid(self) -> None:
        pass

    def refresh_cos(self) -> bool:
        return False

    @property
    def ptt_keyed(self) -> bool:
        return False

    def sync_audio_output_indices_if_changed(
        self, *, after_usb_event: bool = False
    ) -> bool:
        return False


# --- StubRadio matches only CosSnapshotRadio ---


def test_stub_matches_cos_snapshot():
    stub = StubRadio()
    assert as_cos_snapshot(stub) is stub


def test_stub_does_not_match_idle_credit():
    assert as_idle_credit(StubRadio()) is None


def test_stub_does_not_match_clear_wait():
    assert as_clear_wait(StubRadio()) is None


def test_stub_does_not_match_usb_play():
    assert as_usb_play(StubRadio()) is None


def test_stub_does_not_match_cos_polling():
    assert as_cos_polling(StubRadio()) is None


def test_stub_does_not_match_audio_replug():
    assert as_audio_replug(StubRadio()) is None


# --- MinimalRadio matches nothing optional ---


def test_minimal_matches_no_optional():
    radio = _MinimalRadio()
    assert as_idle_credit(radio) is None
    assert as_clear_wait(radio) is None
    assert as_cos_snapshot(radio) is None
    assert as_usb_play(radio) is None
    assert as_cos_polling(radio) is None
    assert as_audio_replug(radio) is None


# --- Single-capability fake ---


def test_idle_credit_fake_matches_only_idle():
    radio = _IdleCreditFake()
    idle = as_idle_credit(radio)
    assert idle is radio
    assert idle is not None
    assert idle.continuous_idle_seconds() == 5.0
    assert as_clear_wait(radio) is None
    assert as_cos_snapshot(radio) is None


# --- Full DRA-like fake ---


def test_full_dra_like_matches_all():
    radio = _FullDraLike()
    assert as_idle_credit(radio) is radio
    assert as_clear_wait(radio) is radio
    assert as_cos_snapshot(radio) is radio
    assert as_usb_play(radio) is radio
    assert as_cos_polling(radio) is radio
    assert as_audio_replug(radio) is radio


# --- RunningOnKeyRadio proxy preserves capabilities ---


def test_proxy_preserves_idle_credit():
    inner = _IdleCreditFake()
    wrapped = RunningOnKeyRadio(inner, on_key=None)
    idle = as_idle_credit(wrapped)
    assert idle is not None
    assert idle.continuous_idle_seconds() == 5.0


def test_proxy_preserves_full_capabilities():
    inner = _FullDraLike()
    wrapped = RunningOnKeyRadio(inner, on_key=None)
    assert as_idle_credit(wrapped) is not None
    assert as_clear_wait(wrapped) is not None
    assert as_cos_snapshot(wrapped) is not None
    assert as_usb_play(wrapped) is not None
    assert as_cos_polling(wrapped) is not None
    assert as_audio_replug(wrapped) is not None


def test_proxy_minimal_matches_nothing():
    inner = _MinimalRadio()
    wrapped = RunningOnKeyRadio(inner, on_key=None)
    assert as_idle_credit(wrapped) is None
    assert as_clear_wait(wrapped) is None


# --- isinstance checks ---


def test_isinstance_checks():
    assert isinstance(_IdleCreditFake(), IdleCreditRadio)
    assert not isinstance(_MinimalRadio(), IdleCreditRadio)
    assert isinstance(_FullDraLike(), ClearWaitRadio)
    assert isinstance(_FullDraLike(), CosPollingRadio)
    assert isinstance(_FullDraLike(), UsbPlayRadio)
    assert isinstance(_FullDraLike(), AudioReplugRadio)
    assert isinstance(StubRadio(), CosSnapshotRadio)
    assert not isinstance(StubRadio(), IdleCreditRadio)
