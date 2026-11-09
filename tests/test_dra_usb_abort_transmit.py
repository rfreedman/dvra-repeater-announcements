"""Abort in-flight transmit when CM119 USB is unplugged."""

from __future__ import annotations

from typing import cast

import numpy as np
import pytest

from app.cm119 import Cm119HidAdapter, HidDeviceEntry, HidModule
from app.dra_playback import play_pcm_chunks_to_dra_devices
from app.dra_radio import DraRadio
from app.radio import TransmitAbortError


def test_playback_abort_raises_transmit_abort_error(monkeypatch: pytest.MonkeyPatch, stub_dra_playback_dependencies: None) -> None:
    _ = stub_dra_playback_dependencies
    abort_after = {"blocks": 2}

    def should_abort() -> bool:
        abort_after["blocks"] -= 1
        return abort_after["blocks"] <= 0

    class FakeStream:
        samplerate: int = 44100

        def __enter__(self) -> FakeStream:
            return self

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: object | None,
        ) -> None:
            pass

        def write_mono_f32(self, mono: np.ndarray) -> None:
            _ = mono
            pass

    def fake_stream(*args: object, **kwargs: object) -> FakeStream:
        _ = args
        _ = kwargs
        return FakeStream()

    monkeypatch.setattr("app.dra_playback.DraUsbPlayback", fake_stream)
    from app.audio import PcmChunk

    chunks = [
        PcmChunk(pcm_int16=b"\x00\x00" * 44100, sample_rate=22050),
    ]
    with pytest.raises(TransmitAbortError):
        play_pcm_chunks_to_dra_devices(
            chunks,
            device_indices=[0],
            should_abort=should_abort,
        )


def test_aplay_playback_honors_should_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    def use_aplay() -> bool:
        return True

    def fake_samplerate(_index: int) -> int:
        return 48000

    def fake_devices(_index: int) -> list[str]:
        return ["hw:1,0"]

    def fake_unmute(_indices: list[int]) -> None:
        pass

    def fake_pulse_unmute(_name: str) -> None:
        pass

    monkeypatch.setattr("app.dra_playback._linux_use_aplay", use_aplay)
    monkeypatch.setattr("app.dra_playback.dra_device_samplerate", fake_samplerate)
    monkeypatch.setattr(
        "app.dra_playback._alsa_playback_devices_for_portaudio_index",
        fake_devices,
    )
    monkeypatch.setattr(
        "app.dra_alsa_unmute.sync_cm119_speaker_unmute_for_indices",
        fake_unmute,
    )
    monkeypatch.setattr(
        "app.dra_pulse_mixer.sync_pulse_sink_unmute_for_portaudio_name",
        fake_pulse_unmute,
    )

    class FakeWriter:
        def write(self, data: bytes) -> None:
            _ = data

        def close(self) -> None:
            pass

    class FakeProc:
        stdin: FakeWriter
        stderr: None
        _done: bool

        def __init__(self) -> None:
            self.stdin = FakeWriter()
            self.stderr = None
            self._done = False

        def poll(self) -> int | None:
            return 0 if self._done else None

        def kill(self) -> None:
            self._done = True

        def wait(self, timeout: float | None = None) -> None:
            _ = timeout
            self._done = True

    proc = FakeProc()
    def fake_popen(*args: object, **kwargs: object) -> FakeProc:
        _ = args
        _ = kwargs
        return proc

    monkeypatch.setattr(
        "app.dra_playback.subprocess.Popen",
        fake_popen,
    )

    abort = {"flag": False}

    def should_abort() -> bool:
        abort["flag"] = True
        return True

    from app.audio import PcmChunk

    chunks = [PcmChunk(pcm_int16=b"\x00\x00" * 100, sample_rate=22050)]
    with pytest.raises(TransmitAbortError):
        play_pcm_chunks_to_dra_devices(
            chunks,
            device_indices=[1],
            should_abort=should_abort,
        )


def test_usb_absent_during_usb_transmit_lead_signals_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    radio.begin_usb_transmit()
    def fake_hid_module() -> HidModule:
        return cast(HidModule, object())

    def no_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return []

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", fake_hid_module)
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", no_entries)
    _ = radio.note_usb_hid_presence()
    assert radio.transmit_abort_is_set()


def test_usb_absent_during_ptt_signals_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    radio.mark_ptt_keyed(True)
    def fake_hid_module() -> HidModule:
        return cast(HidModule, object())

    def no_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return []

    monkeypatch.setattr("app.dra_radio.ensure_hid_module", fake_hid_module)
    monkeypatch.setattr("app.dra_radio.list_cm119_hid_entries", no_entries)
    _ = radio.note_usb_hid_presence()
    assert radio.ptt_keyed is False
    assert radio.transmit_abort_is_set()


def test_aplay_broken_pipe_with_register_proc_is_transmit_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_devices(_index: int) -> list[str]:
        return ["hw:1,0"]

    monkeypatch.setattr(
        "app.dra_playback._alsa_playback_devices_for_portaudio_index",
        fake_devices,
    )

    class BrokenStdin:
        def write(self, _pcm: bytes) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def close(self) -> None:
            pass

    class FakeProc:
        stdin: BrokenStdin = BrokenStdin()
        stderr: None = None

        def poll(self) -> int:
            return 1

        def kill(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> None:
            _ = timeout
            pass

    def fake_popen(*args: object, **kwargs: object) -> FakeProc:
        _ = args
        _ = kwargs
        return FakeProc()

    monkeypatch.setattr("app.dra_playback.subprocess.Popen", fake_popen)
    registered: list[object] = []

    from app.dra_playback import play_s16_stereo_via_aplay

    with pytest.raises(TransmitAbortError):
        play_s16_stereo_via_aplay(
            b"\x00\x00" * 4,
            sample_rate=48000,
            alsa_device="hw:1,0",
            register_proc=registered.append,
        )
    assert len(registered) == 1


def test_handle_fire_runtime_error_dropped_when_usb_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.fire_helpers import make_announcement, make_ctx, make_deps

    monkeypatch.setattr("app.dra_radio.sys.platform", "linux")
    radio = DraRadio(
        hid_adapters=[Cm119HidAdapter(b"path", "dra-hid-1")],
        audio_indices=[0],
        usb_channel="left",
        cos_line="vol_dn",
        cos_active_high=False,
    )
    radio.mark_usb_hid_absent()
    def channel_clear() -> bool:
        return False

    def fail_transmit(*args: object, **kwargs: object) -> None:
        _ = args
        _ = kwargs
        raise RuntimeError("aplay failed on all ALSA devices")

    monkeypatch.setattr(radio, "channel_busy", channel_clear)
    monkeypatch.setattr(
        "app.fire.transmit",
        fail_transmit,
    )
    announcement = make_announcement()
    deps, extras = make_deps(announcement, radio)  # type: ignore[arg-type]
    deps.dra_clear_seconds = 0
    from app.fire import handle_fire

    from app.fire import FireOutcome

    assert handle_fire(make_ctx(), deps) == FireOutcome.DROPPED_USB
    assert extras["played"] == []
