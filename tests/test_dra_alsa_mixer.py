"""CM119 ALSA Speaker unmute at startup (GitHub #28)."""

from __future__ import annotations

import subprocess

import pytest

from app.dra_alsa_mixer import (
    Cm119AlsaWatch,
    alsa_card_from_portaudio_device,
    alsa_cards_for_dra_outputs,
    alsa_watch,
    apply_cm119_replug_recovery,
    ensure_cm119_speakers_unmuted,
    try_unmute_speaker,
    unmute_speaker,
)
from app.startup import StartupError, init_radio_at_startup


def test_alsa_card_from_portaudio_name(monkeypatch):
    def fake_query(index: int) -> dict:
        assert index == 4
        return {"name": "USB PnP Sound Device: Audio (hw:1,0)"}

    monkeypatch.setattr("sounddevice.query_devices", fake_query)
    assert alsa_card_from_portaudio_device(4) == 1


def test_alsa_cards_for_dra_outputs_deduplicates(monkeypatch):
    def fake_card(index: int) -> int | None:
        return {4: 1, 6: 2, 8: 1}.get(index)

    monkeypatch.setattr("app.dra_alsa_mixer.alsa_card_from_portaudio_device", fake_card)
    assert alsa_cards_for_dra_outputs([4, 6, 8]) == [1, 2]


def test_alsa_cards_fallback_proc(monkeypatch):
    monkeypatch.setattr("app.dra_alsa_mixer.alsa_card_from_portaudio_device", lambda _i: None)
    monkeypatch.setattr("app.dra_alsa_mixer._cm119_cards_from_proc", lambda limit: [1, 2][:limit])
    assert alsa_cards_for_dra_outputs([10, 11]) == [1, 2]


def test_unmute_speaker_calls_amixer(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="Simple mixer control 'Speaker',0\n", stderr="")

    monkeypatch.setattr("app.dra_alsa_mixer.subprocess.run", fake_run)
    unmute_speaker(1, "100%")
    assert calls == [["amixer", "-c", "1", "sset", "Speaker", "100%", "unmute"]]


def test_unmute_speaker_failure_raises_startup_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="Invalid card")

    monkeypatch.setattr("app.dra_alsa_mixer.subprocess.run", fake_run)
    with pytest.raises(StartupError, match="card 3"):
        unmute_speaker(3, "100%")


def test_ensure_cm119_speakers_unmuted(monkeypatch):
    unmuted: list[tuple[int, str]] = []

    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    monkeypatch.setattr(
        "app.dra_alsa_mixer.alsa_cards_for_dra_outputs",
        lambda indices: [1, 2] if len(indices) == 2 else [1],
    )

    def record(card: int, level: str) -> None:
        unmuted.append((card, level))

    monkeypatch.setattr("app.dra_alsa_mixer.unmute_speaker", record)
    ensure_cm119_speakers_unmuted([4, 6], level="90%")
    assert unmuted == [(1, "90%"), (2, "90%")]


def test_init_radio_calls_unmute_for_dra_linux(monkeypatch):
    class FakeDra:
        audio_device_indices = [4]

        def warm_open_hid(self) -> None:
            pass

    monkeypatch.setattr("app.config.radio_backend", lambda: "dra")
    monkeypatch.setattr("app.startup.sys.platform", "linux")
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", lambda: True)
    monkeypatch.setattr("app.dra_radio.DraRadio", FakeDra)
    monkeypatch.setattr("app.radio.create_radio_from_config", lambda: FakeDra())
    called: list[list[int]] = []

    def fake_unmute(indices, *, level: str) -> None:
        called.append(list(indices))

    monkeypatch.setattr("app.dra_alsa_mixer.ensure_cm119_speakers_unmuted", fake_unmute)

    init_radio_at_startup()
    assert called == [[4]]


def test_try_unmute_speaker_does_not_raise(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="bad")

    monkeypatch.setattr("app.dra_alsa_mixer.subprocess.run", fake_run)
    assert try_unmute_speaker(2, "100%") is False


def test_alsa_watch_skips_when_signature_unchanged(monkeypatch):
    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    watch = Cm119AlsaWatch()
    watch.note_unmuted_signature((1, 2))
    monkeypatch.setattr("app.dra_alsa_mixer.cm119_alsa_card_signature", lambda limit: (1, 2))
    assert watch.pending_replug_signature(limit=2) is None


def test_apply_replug_recovery_resets_hid_and_unmutes(monkeypatch):
    from app.cm119 import Cm119HidAdapter
    from app.dra_radio import DraRadio

    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    alsa_watch.last_unmuted_signature = (1,)
    monkeypatch.setattr("app.dra_alsa_mixer.cm119_alsa_card_signature", lambda limit: (2,))
    unmuted: list[list[int]] = []
    reset: list[bool] = []
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"old", "dra-hid-1")],
        audio_indices=[4],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    monkeypatch.setattr(radio, "_enumerate_hid_entries", lambda: [{"path": b"old"}])
    monkeypatch.setattr(radio, "sync_hid_adapters_if_replugged", lambda: False)
    monkeypatch.setattr(radio, "reset_hid_after_usb_replug", lambda: reset.append(True))
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", lambda: True)
    monkeypatch.setattr(
        "app.dra_alsa_mixer.try_unmute_cm119_cards",
        lambda cards, level: unmuted.append(cards) or True,
    )
    apply_cm119_replug_recovery(radio)
    assert reset == [True]
    assert unmuted == [[2]]
    assert alsa_watch.last_unmuted_signature == (2,)


def test_ensure_cm119_notes_signature_for_watch(monkeypatch):
    alsa_watch.last_unmuted_signature = None
    monkeypatch.setattr("app.dra_alsa_mixer.sys.platform", "linux")
    monkeypatch.setattr("app.dra_alsa_mixer.alsa_cards_for_dra_outputs", lambda indices: [1, 2])
    monkeypatch.setattr("app.dra_alsa_mixer.unmute_speaker", lambda card, level: None)
    ensure_cm119_speakers_unmuted([4, 6], level="100%")
    assert alsa_watch.last_unmuted_signature == (1, 2)


def test_init_radio_skips_unmute_when_disabled(monkeypatch):
    class FakeDra:
        audio_device_indices = [4]

        def warm_open_hid(self) -> None:
            pass

    monkeypatch.setattr("app.config.radio_backend", lambda: "dra")
    monkeypatch.setattr("app.startup.sys.platform", "linux")
    monkeypatch.setattr("app.config.dra_alsa_unmute_enabled", lambda: False)
    monkeypatch.setattr("app.radio.create_radio_from_config", lambda: FakeDra())

    def boom(*args, **kwargs):
        raise AssertionError("unmute should not run")

    monkeypatch.setattr("app.dra_alsa_mixer.ensure_cm119_speakers_unmuted", boom)
    init_radio_at_startup()
