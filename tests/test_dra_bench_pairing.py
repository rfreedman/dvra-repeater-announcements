from __future__ import annotations

import pytest
from typing import cast

from app.cm119 import HidDeviceEntry, HidModule
from app.dra_bench_pairing import BenchPairingError, resolve_bench_dra_pairs


def test_resolve_bench_dra_pairs_two_of_two(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = [b"\x01", b"\x02"]

    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [
            HidDeviceEntry(
                path=p, vendor_id=0, product_id=0, interface_number=3
            )
            for p in paths
        ]

    def fake_audio_indices() -> list[int]:
        return [10, 11]

    monkeypatch.setattr("app.cm119.list_cm119_hid_entries", fake_entries)
    monkeypatch.setattr("app.dra_portaudio.resolve_dra_audio_indices", fake_audio_indices)
    monkeypatch.setattr("app.config.DRA_HID_PATH_BYTES", None)

    pairs = resolve_bench_dra_pairs(cast(HidModule, object()), min_pairs=2)
    assert len(pairs) == 2
    assert pairs[0].label == "dra-1"
    assert pairs[0].hid_path_hex == "01"
    assert pairs[0].audio_index == 10
    assert pairs[1].audio_index == 11


def test_resolve_bench_dra_pairs_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [HidDeviceEntry(path=b"\x01", vendor_id=0, product_id=0)]

    def fake_audio_indices() -> list[int]:
        return [3, 4]

    monkeypatch.setattr(
        "app.cm119.list_cm119_hid_entries",
        fake_entries,
    )
    monkeypatch.setattr("app.dra_portaudio.resolve_dra_audio_indices", fake_audio_indices)
    monkeypatch.setattr("app.config.DRA_HID_PATH_BYTES", None)

    with pytest.raises(BenchPairingError, match="HID count \\(1\\) != PortAudio"):
        _ = resolve_bench_dra_pairs(cast(HidModule, object()), min_pairs=2)


def test_resolve_bench_dra_pairs_min_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_entries(_hid: HidModule) -> list[HidDeviceEntry]:
        return [HidDeviceEntry(path=b"\x01", vendor_id=0, product_id=0)]

    def fake_audio_indices() -> list[int]:
        return [3]

    monkeypatch.setattr(
        "app.cm119.list_cm119_hid_entries",
        fake_entries,
    )
    monkeypatch.setattr("app.dra_portaudio.resolve_dra_audio_indices", fake_audio_indices)
    monkeypatch.setattr("app.config.DRA_HID_PATH_BYTES", None)

    with pytest.raises(BenchPairingError, match="Need at least 2"):
        _ = resolve_bench_dra_pairs(cast(HidModule, object()), min_pairs=2)
