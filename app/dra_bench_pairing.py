"""Resolve CM119 HID paths to PortAudio indices for dual-DRA bench tests."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DraBenchPair:
    """One DRA-36M: HID entry from enumerate + matching PortAudio output index."""

    hid_entry: dict[str, Any]
    audio_index: int
    label: str

    @property
    def hid_path_hex(self) -> str:
        path = self.hid_entry.get("path")
        return path.hex() if path else ""


class BenchPairingError(RuntimeError):
    """HID/audio count mismatch or fewer adapters than required."""


def resolve_bench_dra_pairs(hid, *, min_pairs: int = 2) -> list[DraBenchPair]:
    """Pair each CM119 HID with a DRA USB audio device (same logic as ``DraRadio.from_config``).

    Ignores ``TTS_DRA_HID_PATH`` (dual bench needs every CM119). Warns if that env is set.
    """
    from app.cm119 import list_cm119_hid_entries
    from app.config import DRA_HID_PATH_BYTES
    from app.dra_playback import resolve_dra_audio_indices

    if DRA_HID_PATH_BYTES is not None:
        log.warning(
            "TTS_DRA_HID_PATH is set (%s); dual bench uses all CM119 HID paths. "
            "Unset it for production with two DRAs.",
            DRA_HID_PATH_BYTES.hex(),
        )
        print(
            "WARNING: TTS_DRA_HID_PATH is set; dual bench ignores it and uses all CM119 paths.",
            flush=True,
        )

    entries = list_cm119_hid_entries(hid)
    try:
        audio_indices = resolve_dra_audio_indices()
    except RuntimeError as exc:
        raise BenchPairingError(str(exc)) from exc

    n_hid = len(entries)
    n_audio = len(audio_indices)
    if n_hid != n_audio:
        raise BenchPairingError(
            f"DRA HID count ({n_hid}) != PortAudio DRA device count ({n_audio}). "
            "Two radios need two of each (run aplay -l, python scripts/dra36m_check.py probe-all). "
            "Unset TTS_DRA_HID_PATH; check udev/plugdev and fixed USB ports."
        )
    if n_hid < min_pairs:
        raise BenchPairingError(
            f"Need at least {min_pairs} paired DRA(s); found {n_hid}. "
            "Plug both adapters into direct NUC USB ports."
        )

    pairs: list[DraBenchPair] = []
    for i, (entry, index) in enumerate(zip(entries, audio_indices, strict=True)):
        pairs.append(DraBenchPair(hid_entry=entry, audio_index=index, label=f"dra-{i + 1}"))
    return pairs
