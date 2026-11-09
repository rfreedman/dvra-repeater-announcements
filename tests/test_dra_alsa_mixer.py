"""CM119 ALSA Speaker unmute at startup (GitHub #28)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import cast

import pytest

from app.dra_alsa_mixer import (
    alsa_card_from_portaudio_device,
    alsa_cards_for_dra_outputs,
    alsa_watch,
    apply_cm119_replug_recovery,
    ensure_cm119_speakers_unmuted,
    try_unmute_speaker,
    unmute_speaker,
)
from app.dra_alsa_unmute import Cm119AlsaWatch
from app.cm119 import HidDeviceEntry, HidModule
from app.startup import StartupError, init_radio_at_startup


def test_alsa_card_from_portaudio_name(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_query(index: int) -> dict[str, object]:
        assert index == 4
        return {"name": "USB PnP Sound Device: Audio (hw:1,0)"}

    monkeypatch.setattr("sounddevice.query_devices", fake_query)
    assert alsa_card_from_portaudio_device(4) == 1


def test_alsa_cards_for_dra_outputs_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_card(index: int) -> int | None:
        return {4: 1, 6: 2, 8: 1}.get(index)

    monkeypatch.setattr("app.dra_alsa_unmute.alsa_card_from_portaudio_device", fake_card)
    assert alsa_cards_for_dra_outputs([4, 6, 8]) == [1, 2]


def test_alsa_cards_fallback_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_card(_index: int) -> None:
        return None

    def fake_cards(limit: int) -> list[int]:
        return [1, 2][:limit]

    monkeypatch.setattr("app.dra_alsa_unmute.alsa_card_from_portaudio_device", no_card)
    monkeypatch.setattr("app.dra_alsa_unmute._cm119_cards_from_proc", fake_cards)
    assert alsa_cards_for_dra_outputs([10, 11]) == [1, 2]


def test_unmute_speaker_calls_amixer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(
        cmd: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        calls.append(list(cmd))
        if cmd[:3] == ["amixer", "-c", "1"] and cmd[3] == "scontents":
            stdout = "Simple mixer control 'Speaker',0\n"
        else:
            stdout = "Simple mixer control 'Speaker',0\n"
        return subprocess.CompletedProcess(list(cmd), 0, stdout=stdout, stderr="")

    monkeypatch.setattr("app.dra_alsa_unmute.subprocess.run", fake_run)
    unmute_speaker(1, "100%")
    assert ["amixer", "-c", "1", "sset", "Speaker", "100%", "unmute"] in calls


def test_unmute_speaker_failure_raises_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        raise subprocess.CalledProcessError(1, list(cmd), stderr="Invalid card")

    monkeypatch.setattr("app.dra_alsa_unmute.subprocess.run", fake_run)
    with pytest.raises(StartupError, match="card 3"):
        unmute_speaker(3, "100%")


def test_ensure_cm119_speakers_unmuted(monkeypatch: pytest.MonkeyPatch) -> None:
    unmuted: list[tuple[int, str]] = []

    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    def fake_cards(indices: list[int]) -> list[int]:
        return [1, 2] if len(indices) == 2 else [1]

    monkeypatch.setattr(
        "app.dra_alsa_unmute.alsa_cards_for_dra_outputs", fake_cards
    )

    def record(card: int, level: str) -> None:
        unmuted.append((card, level))

    monkeypatch.setattr("app.dra_alsa_unmute.unmute_speaker", record)
    ensure_cm119_speakers_unmuted([4, 6], level="90%")
    assert unmuted == [(1, "90%"), (2, "90%")]


def test_init_radio_calls_unmute_for_dra_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDra:
        audio_device_indices: list[int] = [4]

        def warm_open_hid(self) -> None:
            pass

        def sync_audio_output_indices_if_changed(self, *, after_usb_event: bool = False) -> bool:
            _ = after_usb_event
            return False

    def radio_backend() -> str:
        return "dra"

    def unmute_enabled() -> bool:
        return True

    def create_radio() -> FakeDra:
        return FakeDra()

    monkeypatch.setattr("app.config.radio_backend", radio_backend)
    monkeypatch.setattr("app.startup.sys.platform", "linux")
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", unmute_enabled)
    monkeypatch.setattr("app.dra_radio.DraRadio", FakeDra)
    monkeypatch.setattr("app.radio.create_radio_from_config", create_radio)
    called: list[list[int]] = []

    def fake_unmute(indices: list[int], *, level: str) -> None:
        _ = level
        called.append(list(indices))

    monkeypatch.setattr("app.dra_alsa_mixer.ensure_cm119_speakers_unmuted", fake_unmute)

    init_radio_at_startup()
    assert called == [[4]]


def test_try_unmute_speaker_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        raise subprocess.CalledProcessError(1, list(cmd), stderr="bad")

    monkeypatch.setattr("app.dra_alsa_unmute.subprocess.run", fake_run)
    assert try_unmute_speaker(2, "100%") is False


def test_alsa_watch_skips_when_signature_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    watch = Cm119AlsaWatch()
    watch.note_unmuted_signature((1, 2))
    def fake_signature(limit: int) -> tuple[int, ...]:
        _ = limit
        return (1, 2)

    monkeypatch.setattr(
        "app.dra_alsa_unmute.cm119_alsa_card_signature", fake_signature
    )
    assert watch.pending_replug_signature(limit=2) is None


def test_apply_replug_recovery_resets_hid_and_unmutes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.cm119 import Cm119HidAdapter
    from app.dra_radio import DraRadio

    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    alsa_watch.last_unmuted_signature = (1,)
    def fake_signature(limit: int) -> tuple[int, ...]:
        _ = limit
        return (2,)

    def fake_hid_module() -> HidModule:
        return cast(HidModule, object())

    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [{"path": b"old", "vendor_id": 0, "product_id": 0}]

    def hid_unchanged() -> bool:
        return False

    def record_reset() -> None:
        reset.append(True)

    def unmute_cards(cards: list[int], level: str) -> bool:
        _ = level
        unmuted.append(cards)
        return True

    def unmute_enabled() -> bool:
        return True

    monkeypatch.setattr(
        "app.dra_alsa_unmute.cm119_alsa_card_signature", fake_signature
    )
    unmuted: list[list[int]] = []
    reset: list[bool] = []
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"old", "dra-hid-1")],
        audio_indices=[4],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    monkeypatch.setattr("app.dra_radio.ensure_hid_module", fake_hid_module)
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", fake_entries)
    monkeypatch.setattr(radio, "sync_hid_adapters_if_replugged", hid_unchanged)
    monkeypatch.setattr(radio, "reset_hid_after_usb_replug", record_reset)
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", unmute_enabled)
    monkeypatch.setattr(
        "app.dra_alsa_mixer.try_unmute_cm119_cards",
        unmute_cards,
    )
    apply_cm119_replug_recovery(radio)
    assert reset == [True]
    assert unmuted == [[2]]
    assert alsa_watch.last_unmuted_signature == (2,)


def test_ensure_cm119_notes_signature_for_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    alsa_watch.last_unmuted_signature = None
    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    def fake_cards(indices: list[int]) -> list[int]:
        _ = indices
        return [1, 2]

    def fake_unmute(card: int, level: str) -> None:
        _ = card
        _ = level

    monkeypatch.setattr(
        "app.dra_alsa_unmute.alsa_cards_for_dra_outputs", fake_cards
    )
    monkeypatch.setattr("app.dra_alsa_unmute.unmute_speaker", fake_unmute)
    ensure_cm119_speakers_unmuted([4, 6], level="100%")
    assert alsa_watch.last_unmuted_signature == (1, 2)


def test_init_radio_skips_unmute_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDra:
        audio_device_indices: list[int] = [4]

        def warm_open_hid(self) -> None:
            pass

    def radio_backend() -> str:
        return "dra"

    def unmute_enabled() -> bool:
        return False

    def create_radio() -> FakeDra:
        return FakeDra()

    monkeypatch.setattr("app.config.radio_backend", radio_backend)
    monkeypatch.setattr("app.startup.sys.platform", "linux")
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", unmute_enabled)
    monkeypatch.setattr("app.radio.create_radio_from_config", create_radio)

    def boom(*args: object, **kwargs: object) -> None:
        _ = args
        _ = kwargs
        raise AssertionError("unmute should not run")

    monkeypatch.setattr("app.dra_alsa_mixer.ensure_cm119_speakers_unmuted", boom)
    init_radio_at_startup()
