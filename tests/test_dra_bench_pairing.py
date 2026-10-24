from __future__ import annotations

import pytest

from app.dra_bench_pairing import BenchPairingError, resolve_bench_dra_pairs


def test_resolve_bench_dra_pairs_two_of_two(monkeypatch):
    paths = [b"\x01", b"\x02"]

    def fake_entries(_hid):
        return [{"path": p, "interface_number": 3} for p in paths]

    monkeypatch.setattr("app.cm119.list_cm119_hid_entries", fake_entries)
    monkeypatch.setattr("app.dra_playback.resolve_dra_audio_indices", lambda: [10, 11])
    monkeypatch.setattr("app.config.DRA_HID_PATH_BYTES", None)

    pairs = resolve_bench_dra_pairs(object(), min_pairs=2)
    assert len(pairs) == 2
    assert pairs[0].label == "dra-1"
    assert pairs[0].hid_path_hex == "01"
    assert pairs[0].audio_index == 10
    assert pairs[1].audio_index == 11


def test_resolve_bench_dra_pairs_mismatch_raises(monkeypatch):
    monkeypatch.setattr(
        "app.cm119.list_cm119_hid_entries",
        lambda _hid: [{"path": b"\x01"}],
    )
    monkeypatch.setattr("app.dra_playback.resolve_dra_audio_indices", lambda: [3, 4])
    monkeypatch.setattr("app.config.DRA_HID_PATH_BYTES", None)

    with pytest.raises(BenchPairingError, match="HID count \\(1\\) != PortAudio"):
        resolve_bench_dra_pairs(object(), min_pairs=2)


def test_resolve_bench_dra_pairs_min_pairs(monkeypatch):
    monkeypatch.setattr(
        "app.cm119.list_cm119_hid_entries",
        lambda _hid: [{"path": b"\x01"}],
    )
    monkeypatch.setattr("app.dra_playback.resolve_dra_audio_indices", lambda: [3])
    monkeypatch.setattr("app.config.DRA_HID_PATH_BYTES", None)

    with pytest.raises(BenchPairingError, match="Need at least 2"):
        resolve_bench_dra_pairs(object(), min_pairs=2)
