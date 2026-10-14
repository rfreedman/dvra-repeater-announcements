#!/usr/bin/env python3
"""Verify DRA-36M (CM119A) USB audio + HID PTT on the local host.

Uses the Direwolf/hamlib 5-byte HID output report for GPIO3 PTT.
See: https://github.com/nwdigitalradio/direwolf/blob/master/cm108.c

Requires: brew install hidapi (macOS), then `uv sync` in the repo (hidapi, sounddevice, numpy)
On macOS: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib may be needed.

Full test procedures and flags: docs/final-hardware/dra36m-check-tests.md

Examples:
  python scripts/dra36m_check.py smoke              # default left (JU3=L, production)
  python scripts/dra36m_check.py smoke -c right   # only if JU3=R on DRA-36M
  python scripts/dra36m_check.py tone -c left --seconds 4
  python scripts/dra36m_check.py monitor --interval 1 --raw
  python scripts/dra36m_check.py voice "This is a test."
  python scripts/dra36m_check.py retest -c left --with-voice
  python scripts/dra36m_check.py voice-gate -c left --poll 0.25
  python scripts/dra36m_check.py snap --period 0.25
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Shared with the app so bench results match production HID/audio behavior.
from app.cm119 import (  # noqa: E402
    PRODUCT_ID_CM119A,
    VENDOR_ID,
    HidSample,
    read_hid_input_report,
    read_hid_sample,
    set_gpio3_ptt,
)
from app.cm119 import cos_line_busy as _app_cos_line_busy  # noqa: E402
from app.cm119 import open_cm119_hid_at as _open_cm119_hid_at  # noqa: E402
from app.dra_playback import (  # noqa: E402
    DraUsbPlayback,
    auto_select_dra_outputs,
    list_dra_output_devices,
    mono_to_channel,
)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    if sys.platform == "darwin" and os.path.isdir("/opt/homebrew/lib"):
        env.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")
    return env


def _macos_hid_one_shot(*, use_audio: bool) -> bool:
    return sys.platform == "darwin" and not use_audio


# USB stereo channel routed to Mini-DIN DATA IN via DRA JU3 (L or R).
CHANNEL_CHOICES = ("left", "right", "mono")
COS_LINE_CHOICES = ("vol_dn", "vol_up", "allstar3", "dra_mac", "audio_mac", "hybrid_mac")

MONITOR_EPILOG = """\
COS (channel busy) comes from the radio SQL line on Mini-DIN pin 6 (DRA JU1).
Default --cos vol_dn (AllStar active-low on IR0 bit 1). Use --active-high if labels swap.
--cos allstar3 is equivalent when IR0..2 track squelch (IR0..2 all zero = busy).

macOS: while squelch is open, HID may stall — monitor reports busy for vol_dn/allstar3.
HID-only monitor uses a fresh open/read/close each poll (and subprocess fallback) so
idle can return after unkey without restarting the command.

Legacy: --cos dra_mac (non-zero IR0..2 = busy; HID stall = busy) for old bench setups
with JU1 off or before JU5=1200. audio_mac / hybrid_mac do not track squelch on 36M.

Requires JU1 installed, JU5=A (1200), radio volume up, 1200 bps DATA. Ctrl+C to stop.
"""


def add_channel_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--channel",
        choices=CHANNEL_CHOICES,
        default="left",
        help="USB audio channel for DATA IN: left (JU3=L, production default), "
        "right (JU3=R), or mono",
    )


def _ensure_hid():
    if sys.platform == "darwin":
        brew_lib = "/opt/homebrew/lib"
        if os.path.isdir(brew_lib):
            os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", brew_lib)
    try:
        import hid  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SystemExit("Install hidapi: uv sync") from exc
    if not hasattr(hid, "device"):
        raise SystemExit("Wrong 'hid' package installed. Run: uv pip uninstall hid && uv sync")
    return hid


def _cm119_hid_match(hid) -> dict | None:
    candidates: list[dict] = list(hid.enumerate(VENDOR_ID, PRODUCT_ID_CM119A))
    if not candidates:
        candidates = [
            d for d in hid.enumerate(VENDOR_ID, 0) if d.get("vendor_id") == VENDOR_ID
        ]
    if not candidates:
        candidates = [
            d
            for d in hid.enumerate(0, 0)
            if d.get("vendor_id") == VENDOR_ID and d.get("product_id") == PRODUCT_ID_CM119A
        ]
    if not candidates:
        return None
    preferred = [d for d in candidates if d.get("interface_number") == 3]
    if preferred:
        return preferred[0]
    return candidates[0]


def require_cm119_entry(hid, *, wait_seconds: float = 20.0) -> dict:
    """Resolve CM119A HID path; retry when enumerate is empty (common on macOS during COS)."""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    attempt = 0
    while True:
        entry = _cm119_hid_match(hid)
        if entry is not None:
            if attempt > 0:
                print(
                    f"CM119 HID found after {attempt} enumerate attempt(s).",
                    flush=True,
                )
            return entry
        attempt += 1
        if time.monotonic() >= deadline:
            break
        if sys.platform == "darwin" and attempt % 4 == 0:
            _read_hid_via_subprocess()
        time.sleep(min(0.25, 0.05 * attempt))
    if sys.platform == "darwin":
        _read_hid_via_subprocess()
        entry = _cm119_hid_match(hid)
        if entry is not None:
            return entry
    raise SystemExit(
        "No C-Media CM119 HID interface found (is the DRA plugged in?). "
        "If the channel was already busy, close other apps using the DRA, wait a few "
        "seconds, and retry — or start monitor with squelch closed once."
    )


def _open_cm119_hid(hid):
    entry = require_cm119_entry(hid)
    return _open_cm119_hid_at(hid, entry["path"])


def try_open_cm119_hid(hid, path: bytes):
    try:
        return _open_cm119_hid_at(hid, path)
    except OSError:
        return None


def read_hid_input(dev, timeout_ms: int = 300) -> bytes | None:
    return read_hid_sample(dev, timeout_ms).raw


def reopen_cm119_hid(hid, path: bytes):
    """Re-open the same HID path after a glitch; returns None if not available yet."""
    delays = (0.0, 0.2, 0.45, 0.9, 1.5) if sys.platform == "darwin" else (0.0, 0.15, 0.35, 0.75)
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            dev = _open_cm119_hid_at(hid, path)
        except OSError:
            continue
        if read_hid_input_report(dev) is not None:
            return dev
        try:
            dev.close()
        except OSError:
            pass
    return None


def recover_cm119_hid(hid, dev, path: bytes):
    """Close and reopen HID; returns (device, sample) after recovery attempt."""
    try:
        dev.close()
    except OSError:
        pass
    if sys.platform == "darwin":
        time.sleep(0.6)
    new_dev = reopen_cm119_hid(hid, path)
    if new_dev is None:
        new_dev = try_open_cm119_hid(hid, path)
    if new_dev is None:
        return None, HidSample(None, "recover-failed")
    sample = read_hid_sample(new_dev)
    return new_dev, sample


def reacquire_cm119_hid(hid, dev, path: bytes, *, settle_s: float = 1.0):
    """macOS: close HID, wait for squelch to close, open again (like restarting monitor)."""
    if dev is not None:
        try:
            dev.close()
        except (OSError, ValueError):
            pass
    time.sleep(settle_s)
    new_dev = try_open_cm119_hid(hid, path)
    if new_dev is None:
        return None, HidSample(None, "reacquire-failed")
    sample = read_hid_sample(new_dev)
    if sample.raw is None:
        try:
            new_dev.close()
        except (OSError, ValueError):
            pass
        return None, sample
    return new_dev, sample


def sample_cm119_hid(hid, path: bytes) -> HidSample:
    """Open HID, read one COS sample, close (macOS recovers after COS this way)."""

    for delay in (0.0, 0.04, 0.1, 0.22, 0.45):
        if delay:
            time.sleep(delay)
        try:
            dev = _open_cm119_hid_at(hid, path)
        except OSError:
            continue
        try:
            sample = read_hid_sample(dev)
            if sample.raw is not None:
                return sample
        finally:
            try:
                dev.close()
            except OSError:
                pass
            if sys.platform == "darwin":
                time.sleep(0.03)

    sample = HidSample(None, "open-failed")
    if sys.platform == "darwin":
        sub = _read_hid_via_subprocess()
        if sub.raw is not None:
            return sub
    return sample


def _run_voice_subprocess(
    *,
    channel: str,
    lead: float,
    voice: str | None,
    text: str,
) -> int:
    """Run `voice` in a fresh process (macOS: avoids PortAudio -9986 after COS polling)."""
    cmd = [
        sys.executable,
        str(SCRIPT_PATH),
        "voice",
        "-c",
        channel,
        "--lead",
        str(lead),
    ]
    if voice:
        cmd.extend(["-v", voice])
    cmd.append(text)
    try:
        proc = subprocess.run(cmd, env=_subprocess_env(), timeout=300)
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 1
    return int(proc.returncode)


def _read_hid_via_subprocess() -> HidSample:
    """Fresh Python process for one HID read (macOS recovers when in-process handle is stuck)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "hid-once"],
            capture_output=True,
            text=True,
            timeout=8,
            env=_subprocess_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return HidSample(None, "subprocess-fail")
    lines = (proc.stdout or "").strip().splitlines()
    if not lines:
        return HidSample(None, "subprocess-fail")
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return HidSample(None, "subprocess-fail")
    raw_hex = data.get("raw")
    raw = bytes.fromhex(raw_hex) if raw_hex else None
    return HidSample(raw, str(data.get("source", "subprocess")))


def _read_cos_sample(hid, hid_path: bytes | None, dev) -> HidSample:
    """One COS HID sample. macOS HID-only monitor uses open/read/close each poll."""
    if sys.platform == "darwin" and hid_path and dev is None:
        return sample_cm119_hid(hid, hid_path)
    if dev is not None:
        return read_hid_sample(dev)
    if hid_path:
        return sample_cm119_hid(hid, hid_path)
    return HidSample(None, "not-open")


def find_dra_usb_audio():
    """PortAudio index and device dict for the DRA CM119 USB sound device."""
    for index, dev in auto_select_dra_outputs(list_dra_output_devices()):
        if dev["max_output_channels"] >= 1 or dev["max_input_channels"] >= 1:
            return index, dev
    return None, None


def find_dra_audio_device():
    """First DRA output device (same auto-selection as ``resolve_dra_audio_indices``)."""
    for index, dev in auto_select_dra_outputs(list_dra_output_devices()):
        if dev["max_output_channels"] >= 1:
            return index, dev
    return None, None


class DraUsbCapture:
    """One PortAudio input stream (avoids sd.rec + HID fighting the same USB device on macOS)."""

    def __init__(self, device_index: int):
        import sounddevice as sd

        self.device_index = device_index
        info = sd.query_devices(device_index)
        self.samplerate = int(info["default_samplerate"])
        self._stream = None

    def __enter__(self):
        import sounddevice as sd

        self._stream = sd.InputStream(
            device=self.device_index,
            channels=1,
            samplerate=self.samplerate,
            dtype="float32",
        )
        self._stream.start()
        return self

    def __exit__(self, *exc):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def block_rms(self, block_ms: float) -> float:
        import numpy as np

        assert self._stream is not None
        frames = max(1, int(self.samplerate * block_ms / 1000.0))
        data, _overflowed = self._stream.read(frames)
        mono = np.asarray(data, dtype=np.float64).reshape(-1)
        if mono.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(mono * mono)))

    def peak_rms(self, *, blocks: int = 8, block_ms: float = 125.0) -> tuple[float, float]:
        peaks: list[float] = []
        last = 0.0
        for _ in range(max(1, blocks)):
            last = self.block_rms(block_ms)
            peaks.append(last)
        return max(peaks), last


def calibrate_audio_threshold(capture: DraUsbCapture, seconds: float = 2.0) -> float:
    """Noise floor while squelched; suggest threshold ~3x RMS."""
    import numpy as np

    samples = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        samples.append(capture.block_rms(125.0))
        time.sleep(0.15)
    floor = float(np.median(samples)) if samples else 0.0
    return max(floor * 3.0, floor + 0.002, 0.008)


def decode_cos_byte(raw: bytes) -> dict[str, bool | None]:
    """Best-effort COS bits from CM119 input report (AllStar / svxlink conventions)."""
    if len(raw) < 1:
        return {"vol_up": None, "vol_dn": None}
    b = raw[0]
    return {
        "vol_up": bool(b & 0x01),
        "vol_dn": bool(b & 0x02),
    }


def decode_ir0_events(raw: bytes) -> dict[str, bool | None]:
    """CM119 IR0 bits 2-3 latch mute-button edges; can hold non-zero after SQL edges."""
    if len(raw) < 1:
        return {"mute_play_edge": None, "mute_rec_edge": None}
    b = raw[0]
    return {
        "mute_play_edge": bool(b & 0x04),
        "mute_rec_edge": bool(b & 0x08),
    }


def cos_line_busy(
    raw: bytes | None,
    *,
    line: str = "vol_dn",
    active_low: bool = True,
) -> bool | None:
    """Return True if the channel is busy (receiving), False if idle, None if no report."""
    if line == "dra_mac":
        # Legacy macOS bench mode: squelched idle reads all zero; non-zero IR0..2 = busy.
        if not raw or len(raw) < 3:
            return None
        return (raw[0] | raw[1] | raw[2]) != 0
    return _app_cos_line_busy(raw, line=line, active_low=active_low)


def cmd_probe(_args) -> int:
    hid = _ensure_hid()
    idx, audio = find_dra_audio_device()
    print("=== USB audio (PortAudio) ===")
    if audio is None:
        print("FAIL: no DRA-like output device")
    else:
        print(f"OK: device index {idx}: {audio['name']}")
        print(f"    outputs={audio['max_output_channels']} default_sr={audio['default_samplerate']}")

    print("\n=== USB HID (CM119A) ===")
    for d in hid.enumerate(VENDOR_ID, 0):
        if d["vendor_id"] != VENDOR_ID:
            continue
        print(
            f"  vid={d['vendor_id']:04x} pid={d['product_id']:04x} "
            f"if={d.get('interface_number')} product={d.get('product_string')!r}"
        )

    try:
        dev = _open_cm119_hid(hid)
    except SystemExit as e:
        print(f"FAIL: {e}")
        return 1
    print(f"OK: opened HID {dev.get_product_string()}")
    entry = _cm119_hid_match(hid)
    if entry and entry.get("path"):
        print(f"    TTS_DRA_HID_PATH={entry['path'].hex()}  (app single-DRA pin)")
    raw = read_hid_input(dev)
    print(f"    input report (IR0..3): {raw.hex() if raw else '(none)'}")
    if raw:
        print(f"    IR0 COS/button bits: {decode_cos_byte(raw)}")
        print(f"    IR0 edge latch bits: {decode_ir0_events(raw)}")
        print(f"    vol_dn busy: {cos_line_busy(raw, line='vol_dn')}")
        print(f"    allstar3 busy (IR0|1|2==0): {cos_line_busy(raw, line='allstar3')}")
        print("    monitor: --cos vol_dn (use --active-high if idle/busy swap)")
        if raw == bytes(4):
            print(
                "    hint: all zeros and COS never changes → install JU1 (Masters factory default is JU1 off)"
            )
    dev.close()
    return 0 if audio is not None else 1


def cmd_ptt(args) -> int:
    hid = _ensure_hid()
    dev = _open_cm119_hid(hid)
    seconds = args.seconds
    print(f"Keying GPIO3 PTT for {seconds}s — watch the radio TX indicator...")
    set_gpio3_ptt(dev, True)
    print("  PTT keyed")
    time.sleep(seconds)
    set_gpio3_ptt(dev, False)
    print("PTT released.")
    dev.close()
    return 0


def cmd_tone(args) -> int:
    import numpy as np

    idx, audio = find_dra_audio_device()
    if idx is None:
        raise SystemExit("DRA audio device not found")
    if sys.platform.startswith("linux"):
        from app.dra_alsa_mixer import sync_cm119_speaker_unmute_for_indices
        from app.dra_pulse_mixer import sync_pulse_sink_unmute_for_portaudio_index

        sync_cm119_speaker_unmute_for_indices([idx])
        sync_pulse_sink_unmute_for_portaudio_index(idx)
    sr = int(audio["default_samplerate"])
    duration = args.seconds
    freq = args.frequency
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    mono = (args.level * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    peak = float(np.max(np.abs(mono)))
    print(
        f"Playing {freq} Hz on device {idx} ({audio['name']}), "
        f"channel={args.channel}, peak={peak:.4f}"
    )
    with DraUsbPlayback(idx, args.channel, sr) as out:
        out.write_mono_f32(mono)
    return 0


def cmd_smoke(args) -> int:
    import numpy as np

    hid = _ensure_hid()
    idx, audio = find_dra_audio_device()
    if idx is None:
        raise SystemExit("DRA audio device not found")
    if sys.platform.startswith("linux"):
        from app.dra_alsa_mixer import sync_cm119_speaker_unmute_for_indices
        from app.dra_pulse_mixer import sync_pulse_sink_unmute_for_portaudio_index

        sync_cm119_speaker_unmute_for_indices([idx])
        sync_pulse_sink_unmute_for_portaudio_index(idx)
    dev = _open_cm119_hid(hid)
    lead = args.lead
    sr = int(audio["default_samplerate"])
    duration = args.seconds
    freq = args.frequency
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    mono = (args.level * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    lead_frames = np.zeros(int(max(0.0, lead) * sr), dtype=np.float32)
    program = np.concatenate([lead_frames, mono])
    peak = float(np.max(np.abs(program)))

    print(
        f"Smoke test: PortAudio[{idx}] {audio['name']}; "
        f"PTT + tone (channel={args.channel}, lead={lead}s, peak={peak:.4f}). "
        "Low power / dummy load."
    )
    set_gpio3_ptt(dev, True)
    try:
        with DraUsbPlayback(idx, args.channel, sr) as out:
            out.write_mono_f32(program)
    finally:
        set_gpio3_ptt(dev, False)
    raw = read_hid_input(dev)
    print(f"Post-TX input report: {raw.hex() if raw else '(none)'}")
    dev.close()
    print("Done.")
    return 0


def _pcm_chunks_sample_rate(chunks) -> int | None:
    for chunk in chunks:
        if chunk.pcm_int16:
            return chunk.sample_rate
    return None


def play_pcm_chunks_to_dra(
    chunks,
    *,
    device_idx: int,
    channel: str,
    samplerate: int | None = None,
    lead_silence: float = 0.0,
) -> None:
    """Play Piper chunks on one USB stream; optional silence first (PTT lead / COMM OK)."""
    from app.dra_playback import play_pcm_chunks_to_single_dra

    play_pcm_chunks_to_single_dra(
        chunks,
        device_index=device_idx,
        channel=channel,
        lead_silence=lead_silence,
    )


VOICE_GATE_EPILOG = """\
Bench test for production-style busy gating (same idea as the app's defer-until-clear):

  1. Start this command (squelch closed on the monitored IC-2720).
  2. Key the *other* radio and hold — script must stay silent (no PTT).
  3. Unkey — script waits for continuous idle (--clear-seconds, default 10s).
  4. If COS stays idle for that long, transmit Piper once and exit.
     Any busy during the clear window resets the timer (no stepping on follow-up QSOs).

Pre-synthesizes speech for the wait phases. On macOS, the final TX runs `voice` in a
subprocess (clean PortAudio + HID). Ctrl+C to abort.
"""


def _cos_busy_from_sample(
    raw: bytes | None,
    *,
    cos: str,
    active_low: bool,
) -> bool | None:
    if raw is not None:
        return cos_line_busy(raw, line=cos, active_low=active_low)
    if cos == "dra_mac":
        return True
    # macOS: GET_INPUT_REPORT often fails while squelch is open even when vol_dn bytes
    # are valid on idle (probe/snap may still see bytes between stalls).
    if sys.platform == "darwin" and cos in ("vol_dn", "allstar3"):
        return True
    return None


def _poll_cos_busy(
    hid,
    hid_path: bytes | None,
    dev,
    *,
    cos: str,
    active_low: bool,
) -> bool | None:
    sample = _read_cos_sample(hid, hid_path, dev)
    return _cos_busy_from_sample(sample.raw, cos=cos, active_low=active_low)


def _hid_stall_means_busy(raw: bytes | None, *, cos: str) -> bool:
    return raw is None and (
        cos == "dra_mac"
        or (sys.platform == "darwin" and cos in ("vol_dn", "allstar3"))
    )


def _transmit_voice_on_dra(
    dev,
    *,
    device_idx: int,
    channel: str,
    lead: float,
    chunks,
    piper_rate: int,
) -> None:
    set_gpio3_ptt(dev, True)
    try:
        play_pcm_chunks_to_dra(
            chunks,
            device_idx=device_idx,
            channel=channel,
            samplerate=piper_rate,
            lead_silence=lead,
        )
    finally:
        set_gpio3_ptt(dev, False)


def _voice_text_from_args(text_arg: str | list[str] | None) -> str:
    """CLI passes words as a list; retest passes a single string — do not join(str) by char."""
    if not text_arg:
        return ""
    if isinstance(text_arg, str):
        return text_arg.strip()
    return " ".join(text_arg).strip()


def cmd_voice(args) -> int:
    """PTT lead + Piper TTS through DRA USB audio + unkey (bench on-air test)."""

    from app.registry import get_registry

    hid = _ensure_hid()
    idx, audio = find_dra_audio_device()
    if idx is None:
        raise SystemExit("DRA audio device not found")
    dev = _open_cm119_hid(hid)
    text = _voice_text_from_args(args.text)
    if not text:
        text = "This is a test announcement from the Delaware Valley Radio Association."

    print(f"Synthesizing Piper voice… ({len(text)} chars)")
    registry = get_registry()
    voice_id, piper_rate, chunks = registry.synthesize(text, voice=args.voice)
    dra_sr = int(audio["default_samplerate"])
    print(
        f"Voice test: PTT + Piper ({voice_id}, {piper_rate} Hz → {dra_sr} Hz USB) on device {idx} "
        f"({audio['name']}), channel={args.channel}, lead={args.lead}s silence on air "
        "before speech — low power / dummy load."
    )
    _transmit_voice_on_dra(
        dev,
        device_idx=idx,
        channel=args.channel,
        lead=args.lead,
        chunks=chunks,
        piper_rate=piper_rate,
    )
    raw = read_hid_input(dev)
    print(f"Post-TX input report: {raw.hex() if raw else '(none)'}")
    dev.close()
    print("Done.")
    return 0


def cmd_voice_gate(args) -> int:
    """Wait for COS busy (other station), defer TX, then Piper once when idle."""

    from app.registry import get_registry

    hid = _ensure_hid()
    entry = require_cm119_entry(hid, wait_seconds=args.wait_device)
    hid_path = entry["path"]
    idx, audio = find_dra_audio_device()
    if idx is None:
        raise SystemExit("DRA audio device not found")
    dev = None if sys.platform == "darwin" else _open_cm119_hid(hid)
    text = _voice_text_from_args(args.text)
    if not text:
        text = "The channel is clear. This is a deferred test announcement."

    active_low = not args.active_high
    cos = args.cos
    poll = args.poll

    registry = get_registry()
    if sys.platform == "darwin":
        voice_id = registry.prepare(args.voice)
        piper_rate = 0
        chunks = []
        print(
            f"Voice gate: {voice_id}, --cos {cos}, poll={poll}s, "
            f"clear={args.clear_seconds}s, channel={args.channel}, lead={args.lead}s "
            f"({len(text)} chars; Piper via voice subprocess at TX)",
            flush=True,
        )
    else:
        print(f"Synthesizing Piper voice… ({len(text)} chars)", flush=True)
        voice_id, piper_rate, chunks = registry.synthesize(text, voice=args.voice)
        print(
            f"Voice gate: {voice_id} @ {piper_rate} Hz, --cos {cos}, poll={poll}s, "
            f"clear={args.clear_seconds}s, channel={args.channel}, lead={args.lead}s",
            flush=True,
        )
    print(
        "Step 1: Key the OTHER radio and hold. This rig must NOT transmit while COS is busy.",
        flush=True,
    )

    deadline = time.monotonic() + args.wait_busy
    saw_busy = False
    last_log = 0.0
    while time.monotonic() < deadline:
        busy = _poll_cos_busy(hid, hid_path, dev, cos=cos, active_low=active_low)
        if busy is True:
            saw_busy = True
            print("COS busy — deferring (no PTT). Hold the other key until you are ready to unkey.", flush=True)
            break
        now = time.monotonic()
        if busy is False and now - last_log >= 2.0:
            print("COS idle — waiting for you to key the other radio…", flush=True)
            last_log = now
        time.sleep(poll)

    if not saw_busy:
        if dev is not None:
            dev.close()
        raise SystemExit(
            f"Timed out ({args.wait_busy}s): COS never went busy. Key the other radio while this runs."
        )

    busy_since = time.monotonic()
    deadline = time.monotonic() + args.wait_idle
    last_busy_log = 0.0
    last_clear_log = 0.0
    phase = "hold"
    clear_ready_at: float | None = None
    while time.monotonic() < deadline:
        busy = _poll_cos_busy(hid, hid_path, dev, cos=cos, active_low=active_low)
        now = time.monotonic()
        if busy is True:
            if phase == "clear":
                print("COS busy again — reset clear wait (will not step on this TX).", flush=True)
            phase = "hold"
            clear_ready_at = None
            busy_since = now
            if now - last_busy_log >= 2.0:
                print("COS busy — not transmitting.", flush=True)
                last_busy_log = now
        elif busy is False:
            if phase == "hold":
                held = now - busy_since
                if held < args.min_busy:
                    time.sleep(poll)
                    continue
                phase = "clear"
                clear_ready_at = now + args.clear_seconds
                print(
                    f"COS idle after {held:.1f}s busy — need {args.clear_seconds}s "
                    "continuous clear before TX…",
                    flush=True,
                )
                last_clear_log = now
            elif phase == "clear" and clear_ready_at is not None:
                remaining = clear_ready_at - now
                if remaining <= 0:
                    print(
                        f"Clear for {args.clear_seconds}s — transmitting once.",
                        flush=True,
                    )
                    if sys.platform == "darwin":
                        rc = _run_voice_subprocess(
                            channel=args.channel,
                            lead=args.lead,
                            voice=args.voice,
                            text=text,
                        )
                        if rc != 0:
                            raise SystemExit(
                                f"voice-gate TX failed (voice subprocess exit {rc}). "
                                "Try: python scripts/dra36m_check.py voice -c "
                                f"{args.channel} --lead {args.lead} manually."
                            )
                    else:
                        tx_dev = dev if dev is not None else _open_cm119_hid(hid)
                        try:
                            _transmit_voice_on_dra(
                                tx_dev,
                                device_idx=idx,
                                channel=args.channel,
                                lead=args.lead,
                                chunks=chunks,
                                piper_rate=piper_rate,
                            )
                            raw = read_hid_input(tx_dev)
                            print(
                                f"Post-TX input report: {raw.hex() if raw else '(none)'}",
                                flush=True,
                            )
                        finally:
                            if dev is None:
                                tx_dev.close()
                    print(
                        "Done — PASS if TX started only after clear window, not during other QSO.",
                        flush=True,
                    )
                    return 0
                if now - last_clear_log >= 2.0:
                    print(f"Clear wait… {remaining:.1f}s remaining.", flush=True)
                    last_clear_log = now
        time.sleep(poll)

    if dev is not None:
        dev.close()
    raise SystemExit(
        f"Timed out ({args.wait_idle}s): never got {args.clear_seconds}s clear after busy. "
        "Unkey and keep channel quiet, or extend --wait-idle."
    )


def cmd_retest(args) -> int:
    """Run probe, PTT, tone smoke, and optional Piper voice (single-radio bench)."""
    print("=== retest: probe ===")
    if cmd_probe(argparse.Namespace()) != 0:
        return 1

    hid = _ensure_hid()
    dev = _open_cm119_hid(hid)
    raw_idle = read_hid_input(dev)
    busy_idle = cos_line_busy(raw_idle, line="vol_dn", active_low=not args.active_high)
    print(f"=== retest: COS idle sample ir0..3={raw_idle.hex() if raw_idle else 'none'} "
          f"vol_dn_busy={busy_idle} (expect False) ===")
    if busy_idle:
        print("WARNING: channel reads busy while squelched; check SQL / JU1 / JU5", flush=True)
    dev.close()

    print("=== retest: PTT 0.5s ===")
    if cmd_ptt(argparse.Namespace(seconds=0.5)) != 0:
        return 1

    print("=== retest: smoke (tone) ===")
    smoke_ns = argparse.Namespace(
        lead=args.lead,
        seconds=args.smoke_seconds,
        frequency=800.0,
        level=0.3,
        channel=args.channel,
    )
    if cmd_smoke(smoke_ns) != 0:
        return 1

    if args.with_voice:
        print("=== retest: voice (Piper) ===")
        voice_ns = argparse.Namespace(
            text=[args.voice_text],
            voice=args.voice,
            lead=args.lead,
            channel=args.channel,
        )
        if cmd_voice(voice_ns) != 0:
            return 1

    print("=== retest: PASS (manual: confirm on-air audio / deviation) ===")
    return 0


def cmd_hid_once(_args) -> int:
    """Single HID read; JSON on stdout for monitor subprocess on macOS."""
    hid = _ensure_hid()
    entry = _cm119_hid_match(hid)
    if entry is None:
        print(json.dumps({"raw": None, "source": "no-device"}))
        return 1
    dev = _open_cm119_hid_at(hid, entry["path"])
    try:
        sample = read_hid_sample(dev)
        payload = {
            "raw": sample.raw.hex() if sample.raw else None,
            "source": sample.source,
        }
        print(json.dumps(payload))
        return 0 if sample.raw is not None else 2
    finally:
        try:
            dev.close()
        except (OSError, ValueError):
            pass


def cmd_snap(args) -> int:
    """Rapid HID samples for bench COS debugging."""
    hid = _ensure_hid()
    entry = require_cm119_entry(hid)
    hid_path = entry["path"]
    use_one_shot = _macos_hid_one_shot(use_audio=False)
    dev = None if use_one_shot else _open_cm119_hid_at(hid, hid_path)
    count = args.count
    period = args.period
    print(f"Sampling COS {count} times every {period}s (Ctrl+C to stop early)")
    try:
        for n in range(count):
            sample = (
                _read_cos_sample(hid, hid_path, dev)
                if use_one_shot
                else read_hid_sample(dev)
            )
            raw = sample.raw
            active_low = not args.active_high
            busy_dn = _cos_busy_from_sample(raw, cos="vol_dn", active_low=active_low)
            busy3 = _cos_busy_from_sample(raw, cos="allstar3", active_low=active_low)
            hex_part = raw.hex() if raw else "none"
            print(
                f"{n:3d}  src={sample.source}  ir0..3={hex_part}  vol_dn={busy_dn}  "
                f"allstar3={busy3}  bits={decode_cos_byte(raw) if raw else {}}",
                flush=True,
            )
            if n + 1 < count:
                time.sleep(period)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if dev is not None:
            dev.close()
    return 0


def cmd_monitor(args) -> int:
    hid = _ensure_hid()
    if args.cos in ("audio_mac",):
        entry = _cm119_hid_match(hid)
    else:
        entry = require_cm119_entry(hid, wait_seconds=args.wait_device)
    hid_path = entry["path"] if entry else b""
    use_audio = args.cos in ("audio_mac", "hybrid_mac")
    audio_idx, audio_dev = find_dra_usb_audio()
    if use_audio:
        if audio_idx is None or audio_dev["max_input_channels"] < 1:
            raise SystemExit("DRA USB capture input not found (need CM119 recording channel)")

    macos_hid_one_shot = _macos_hid_one_shot(use_audio=use_audio)
    dev = None
    if not use_audio and entry is not None and not macos_hid_one_shot:
        dev = _open_cm119_hid_at(hid, hid_path)
    active_low = not args.active_high
    interval = args.interval
    cos_label = args.cos
    if cos_label not in ("allstar3", "dra_mac", "audio_mac", "hybrid_mac"):
        cos_label = f"{args.cos}, active_low={active_low}"
    print(f"Monitoring COS ({cos_label}) every {interval}s — Ctrl+C to stop")
    if use_audio:
        print(
            "  (USB capture does NOT follow squelch on DRA-36M; use --cos vol_dn)",
            flush=True,
        )
    elif args.cos == "dra_mac":
        print(
            "  (legacy dra_mac: non-zero IR0..2 or HID stall = busy; prefer --cos vol_dn)",
            flush=True,
        )
    elif macos_hid_one_shot:
        print(
            "  (macOS: fresh HID open/read/close each poll — idle should return after unkey)",
            flush=True,
        )
    elif (
        sys.platform == "darwin"
        and args.cos in ("dra_mac", "vol_dn", "allstar3")
        and args.reacquire_after > 0
    ):
        print(
            f"  (macOS: --reacquire-after {args.reacquire_after}s on persistent HID handle)",
            flush=True,
        )
    last_sample: bytes | None = None
    last_printed_key: object = None
    last_change = time.monotonic()
    read_failures = 0
    mac_stuck_hint = False
    none_since: float | None = None
    last_reacquire_at = 0.0
    last_audio_hot_at = 0.0
    audio_threshold = args.audio_threshold or 0.01
    try:
        capture_ctx = (
            DraUsbCapture(audio_idx) if use_audio and audio_idx is not None else nullcontext()
        )
        with capture_ctx as capture:
            if use_audio and capture is not None:
                if args.calibrate_audio or args.audio_threshold is None:
                    print(
                        f"Calibrating audio noise floor ({args.calibrate_seconds}s, squelch closed)…",
                        flush=True,
                    )
                    audio_threshold = calibrate_audio_threshold(
                        capture, seconds=args.calibrate_seconds
                    )
                    print(f"  Using --audio-threshold {audio_threshold:.5f}", flush=True)

            next_tick = time.monotonic()
            while True:
                audio_peak: float | None = None
                audio_rms: float | None = None
                if use_audio and capture is not None:
                    audio_peak, audio_rms = capture.peak_rms(
                        blocks=args.audio_peak_blocks,
                        block_ms=args.audio_block_ms,
                    )
                    if audio_peak > audio_threshold:
                        last_audio_hot_at = time.monotonic()

                sample = HidSample(None, "audio-only" if use_audio else "skipped")
                raw = None
                if use_audio:
                    pass
                elif macos_hid_one_shot:
                    sample = _read_cos_sample(hid, hid_path, None)
                    raw = sample.raw
                elif dev is not None:
                    sample = read_hid_sample(dev)
                    raw = sample.raw

                now = time.monotonic()
                if raw is not None:
                    none_since = None
                elif none_since is None:
                    none_since = now

                if (
                    not macos_hid_one_shot
                    and sys.platform == "darwin"
                    and args.cos in ("dra_mac", "vol_dn", "allstar3")
                    and args.reacquire_after > 0
                    and dev is not None
                    and raw is None
                    and none_since is not None
                    and now - none_since >= args.reacquire_after
                    and now - last_reacquire_at >= args.reacquire_after
                ):
                    last_reacquire_at = now
                    print("  → macOS: reacquiring HID (post-COS stall)…", flush=True)
                    new_dev, sample = reacquire_cm119_hid(
                        hid, dev, hid_path, settle_s=min(1.0, args.reacquire_after)
                    )
                    if new_dev is not None:
                        dev = new_dev
                        raw = sample.raw
                        none_since = None if raw is not None else now
                        if raw is not None:
                            print(
                                "  → macOS: HID read restored (check idle/busy line)",
                                flush=True,
                            )

                if args.cos in ("audio_mac", "hybrid_mac"):
                    now_mono = time.monotonic()
                    above = audio_peak is not None and audio_peak > audio_threshold
                    hang = (now_mono - last_audio_hot_at) < args.audio_hang
                    busy = above or hang
                else:
                    busy = _cos_busy_from_sample(
                        raw, cos=args.cos, active_low=active_low
                    )

                if (
                    raw is None
                    and not _hid_stall_means_busy(raw, cos=args.cos)
                    and not use_audio
                ):
                    read_failures += 1
                    if read_failures >= 12 and sys.platform != "darwin":
                        print(
                            "  → HID read failed repeatedly; reopening same USB path…",
                            flush=True,
                        )
                        new_dev, sample = recover_cm119_hid(hid, dev, hid_path)
                        dev = new_dev
                        read_failures = 0
                        raw = sample.raw
                        busy = cos_line_busy(raw, line=args.cos, active_low=active_low)
                else:
                    read_failures = 0

                if (
                    not macos_hid_one_shot
                    and sys.platform == "darwin"
                    and args.cos in ("dra_mac", "vol_dn", "allstar3")
                    and raw is None
                    and time.monotonic() - last_change >= 12.0
                    and not mac_stuck_hint
                ):
                    mac_stuck_hint = True
                    print(
                        "  → macOS: still busy after unkey? Try restarting monitor, or use "
                        "NUC/Linux for authoritative COS.",
                        flush=True,
                    )

                state_key = audio_peak if use_audio else raw
                if state_key != last_sample:
                    last_sample = state_key
                    last_change = time.monotonic()
                    if raw is not None:
                        mac_stuck_hint = False
                ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
                if busy is None:
                    line = f"{ts} unknown (no HID report)"
                elif busy:
                    if _hid_stall_means_busy(raw, cos=args.cos):
                        line = f"{ts} busy (HID read stalled; COS likely active)"
                    else:
                        line = f"{ts} busy"
                else:
                    line = f"{ts} idle"
                if args.raw:
                    if use_audio:
                        line = f"{line}  src={sample.source}"
                    else:
                        bits = decode_cos_byte(raw) if raw else {"vol_up": None, "vol_dn": None}
                        hex_part = raw.hex() if raw else "none"
                        edges = decode_ir0_events(raw) if raw else {}
                        ir1_gpio = None
                        if raw and len(raw) > 1:
                            ir1_gpio = raw[1] & 0x0F
                        line = (
                            f"{line}  src={sample.source}  ir0..3={hex_part}  ir0_bits={bits}  "
                            f"ir0_edges={edges}"
                        )
                        if ir1_gpio is not None:
                            line = f"{line}  ir1_gpio_lo={ir1_gpio:#04x}"
                if use_audio and audio_peak is not None:
                    line = (
                        f"{line}  audio_peak={audio_peak:.5f}  audio_last={audio_rms:.5f}  "
                        f"thr={audio_threshold:.5f}  hang={args.audio_hang:.1f}s"
                    )
                print_key = (raw, sample.source, busy, audio_peak)
                if not args.changes_only or print_key != last_printed_key:
                    print(line, flush=True)
                    last_printed_key = print_key

                next_tick += interval
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_tick = time.monotonic()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if dev is not None:
            try:
                dev.close()
            except (OSError, ValueError):
                pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DRA-36M / CM119A connection checks")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="List USB audio + HID; read idle COS byte")
    p_probe.set_defaults(func=cmd_probe)

    p_hid_once = sub.add_parser(
        "hid-once",
        help=argparse.SUPPRESS,
    )
    p_hid_once.set_defaults(func=cmd_hid_once)

    p_snap = sub.add_parser("snap", help="Rapid COS samples while you key from another radio")
    p_snap.add_argument("--count", type=int, default=30, help="Number of samples (default: 30)")
    p_snap.add_argument("--period", type=float, default=0.25, help="Seconds between samples")
    p_snap.add_argument("--active-high", action="store_true")
    p_snap.set_defaults(func=cmd_snap)

    p_ptt = sub.add_parser("ptt", help="Key GPIO3 for N seconds")
    p_ptt.add_argument("--seconds", type=float, default=1.0)
    p_ptt.set_defaults(func=cmd_ptt)

    p_tone = sub.add_parser("tone", help="Play test tone to DRA USB audio")
    p_tone.add_argument("--seconds", type=float, default=1.5)
    p_tone.add_argument("--frequency", type=float, default=1000.0)
    p_tone.add_argument("--level", type=float, default=0.25)
    add_channel_argument(p_tone)
    p_tone.set_defaults(func=cmd_tone)

    p_smoke = sub.add_parser("smoke", help="PTT lead + tone + unkey")
    p_smoke.add_argument(
        "--lead",
        type=float,
        default=0.4,
        help="Seconds of silence after PTT before tone (default: 0.4)",
    )
    p_smoke.add_argument("--seconds", type=float, default=4.0)
    p_smoke.add_argument("--frequency", type=float, default=800.0)
    p_smoke.add_argument("--level", type=float, default=0.3)
    add_channel_argument(p_smoke)
    p_smoke.set_defaults(func=cmd_smoke)

    p_voice = sub.add_parser("voice", help="PTT lead + Piper TTS to DRA USB audio")
    p_voice.add_argument("text", nargs="*", help="Text to synthesize and transmit")
    p_voice.add_argument("-v", "--voice", help="Piper voice id or alias")
    p_voice.add_argument(
        "--lead",
        type=float,
        default=0.4,
        help="Seconds of silence after PTT before speech (COMM OK + radio settle; default: 0.4)",
    )
    add_channel_argument(p_voice)
    p_voice.set_defaults(func=cmd_voice)

    p_voice_gate = sub.add_parser(
        "voice-gate",
        help="Defer Piper TX while COS busy; transmit once when idle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=VOICE_GATE_EPILOG,
    )
    p_voice_gate.add_argument("text", nargs="*", help="Text to speak after channel clears")
    p_voice_gate.add_argument("-v", "--voice", help="Piper voice id or alias")
    p_voice_gate.add_argument(
        "--lead",
        type=float,
        default=0.6,
        help="Silence after PTT before speech (default: 0.6)",
    )
    p_voice_gate.add_argument(
        "--poll",
        type=float,
        default=0.25,
        metavar="SEC",
        help="COS poll interval in seconds (default: 0.25)",
    )
    p_voice_gate.add_argument(
        "--wait-device",
        type=float,
        default=20.0,
        metavar="SEC",
        help="Retry USB HID enumerate at startup (default: 20)",
    )
    p_voice_gate.add_argument(
        "--wait-busy",
        type=float,
        default=120.0,
        metavar="SEC",
        help="Max wait for COS to go busy (default: 120)",
    )
    p_voice_gate.add_argument(
        "--wait-idle",
        type=float,
        default=300.0,
        metavar="SEC",
        help="Max wait for COS idle after busy (default: 300)",
    )
    p_voice_gate.add_argument(
        "--min-busy",
        type=float,
        default=0.5,
        metavar="SEC",
        help="Require COS busy at least this long before clear timer can start (default: 0.5)",
    )
    p_voice_gate.add_argument(
        "--clear-seconds",
        type=float,
        default=10.0,
        metavar="SEC",
        help="Continuous COS idle required before TX; resets if busy returns (default: 10)",
    )
    p_voice_gate.add_argument(
        "--cos",
        choices=("vol_dn", "allstar3", "dra_mac"),
        default="vol_dn",
        help="COS decode mode (default: vol_dn)",
    )
    p_voice_gate.add_argument(
        "--active-high",
        action="store_true",
        help="Invert vol_dn polarity",
    )
    add_channel_argument(p_voice_gate)
    p_voice_gate.set_defaults(func=cmd_voice_gate)

    p_retest = sub.add_parser(
        "retest",
        help="Probe, COS idle sample, PTT, smoke tone, optional Piper voice",
    )
    p_retest.add_argument(
        "--with-voice",
        action="store_true",
        help="Also run Piper voice on-air test",
    )
    p_retest.add_argument(
        "--voice-text",
        default="This is a test announcement from the Delaware Valley Radio Association.",
        help="Text for --with-voice",
    )
    p_retest.add_argument("-v", "--voice", help="Piper voice id or alias")
    p_retest.add_argument("--lead", type=float, default=0.4)
    p_retest.add_argument("--smoke-seconds", type=float, default=2.0)
    p_retest.add_argument("--active-high", action="store_true")
    add_channel_argument(p_retest)
    p_retest.set_defaults(func=cmd_retest)

    p_monitor = sub.add_parser(
        "monitor",
        help="Poll COS (channel busy) from HID every N seconds",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=MONITOR_EPILOG,
    )
    p_monitor.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between samples (default: 1)",
    )
    p_monitor.add_argument(
        "--wait-device",
        type=float,
        default=20.0,
        metavar="SEC",
        help="Retry USB HID enumerate up to SEC seconds at startup (default: 20)",
    )
    p_monitor.add_argument(
        "--cos",
        choices=COS_LINE_CHOICES,
        default="vol_dn",
        help="COS mode (default: vol_dn)",
    )
    p_monitor.add_argument(
        "--audio-threshold",
        type=float,
        default=None,
        metavar="RMS",
        help="USB capture RMS busy threshold (audio_mac/hybrid_mac; default: auto-calibrate)",
    )
    p_monitor.add_argument(
        "--calibrate-audio",
        action="store_true",
        help="Measure squelched noise floor before monitoring (audio_mac/hybrid_mac)",
    )
    p_monitor.add_argument(
        "--calibrate-seconds",
        type=float,
        default=2.0,
        help="Seconds for audio calibration (default: 2)",
    )
    p_monitor.add_argument(
        "--audio-hang",
        type=float,
        default=3.0,
        metavar="SEC",
        help="Stay busy this long after last above-threshold peak (default: 3)",
    )
    p_monitor.add_argument(
        "--audio-peak-blocks",
        type=int,
        default=8,
        help="Capture blocks per poll; peak RMS used (default: 8)",
    )
    p_monitor.add_argument(
        "--audio-block-ms",
        type=float,
        default=125.0,
        help="Milliseconds per capture block (default: 125)",
    )
    p_monitor.add_argument(
        "--active-high",
        action="store_true",
        help="Treat COS bit high as busy (default: active-low / AllStar-style)",
    )
    p_monitor.add_argument(
        "--raw",
        action="store_true",
        help="Include raw HID hex and vol_up/vol_dn bits",
    )
    p_monitor.add_argument(
        "--changes-only",
        action="store_true",
        help="Print a line only when ir0..3 changes",
    )
    p_monitor.add_argument(
        "--reacquire-after",
        type=float,
        default=0.0,
        metavar="SEC",
        help="macOS only (persistent HID): reopen after SEC seconds of stall (default 0; "
        "monitor normally uses one-shot HID reads on macOS)",
    )
    p_monitor.set_defaults(func=cmd_monitor)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
