# Bench record (DRA + radio checkout)

Copy this file or extend it when moving from dev (Mac) to production (NUC). Values below document the **Mac + IC-2720** path verified in October 2026. **Production (both radios): JU3 = L, USB left channel, R16.** Fill **NUC / IC-207H** sections when that hardware is available.

**How to run each test:** [`dra36m-check-tests.md`](dra36m-check-tests.md) (probe, monitor, snap, smoke, voice, voice-gate, retest, flags).

---

## DRA-36M (unit under test)

| Item | Mac / IC-2720 dev value | Notes |
|------|---------------------------|--------|
| **JU1** | Installed | Factory = off |
| **JU5** | **A (1200)** | Factory = B (9600) |
| **JU3** | **L (left)** | Pin 1 ← USB left channel |
| **PTT pair** | **T** (transistor) | Switch to **R** if a radio will not key |
| **JU4** | Off (default) | — |
| **TX level pot** | **R16** (left / JU3=L) | Backed off from factory until Piper voice was clear (no fuzzy over-deviation). *Record your panel mark:* _________________ |
| **USB** | Direct to MacBook (no hub) | `probe` OK; PortAudio index **2** (“USB PnP Sound Device”) |
| **Blue COMM OK** | Off during HID-only tests | Normal; streams during `smoke` / `voice` |

---

## Software / COS (Mac)

| Item | Value |
|------|--------|
| **COS mode** | `monitor --cos vol_dn` |
| **Polarity** | Default active-low — **no** `--active-high` |
| **Idle HID (squelched)** | `ir0..3=02000000`, `vol_dn busy: False` |
| **Cross-check** | `allstar3` matched `vol_dn` in testing |
| **Bare `ptt` subcommand** | Does not key RF (no USB audio → COMM OK watchdog) |
| **RF PTT tests** | Use `smoke` or `voice` (audio + GPIO3) |

---

## Software / transmit (Mac)

| Item | Value |
|------|--------|
| **USB channel flag** | `-c left` (matches **JU3 = L**) |
| **PTT lead** | `--lead 0.6` (silence on air before speech; tune 0.5–0.8 if needed) |
| **Piper voice** | `en_US-ryan-medium` @ **22050 Hz** (stream at Piper rate, not 48 kHz device default) |
| **Regression** | `python scripts/dra36m_check.py retest -c left --with-voice --lead 0.6` |
| **Busy gate** | `voice-gate -c left --poll 0.25 --clear-seconds 10 --lead 0.6` |
| **macOS COS monitor** | One-shot HID per poll; stall while busy → **busy** line; idle after unkey |
| **macOS voice-gate TX** | **`voice`** subprocess at end (same `-c` / `--lead` / text) |

macOS env when `hidapi` is from Homebrew:

```bash
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
```

---

## IC-2720 (development radio only)

| Item | Value | Your notes |
|------|--------|------------|
| **Role** | Dev bench; production target is **IC-207H** | |
| **Frequency** | 146.520 MHz simplex (2 m calling) | |
| **DATA rate** | **1200 bps** (not 9600) | |
| **Band / mode** | | e.g. VFO, packet/DATA band as set on rig |
| **SQL (P SQL)** | | Knob position when COS tracked reliably |
| **Speaker volume** | Up (pin 6 / COS) | |
| **Mic** | Unplug for on-air `voice` tests @ 1200 | |

---

## NUC + IC-207H (fill in on site)

| Item | Radio 1 | Radio 2 |
|------|---------|---------|
| NUC USB port (fixed) | | |
| udev / ALSA / PortAudio name | | |
| hidraw path | | |
| DRA label (Radio 1 / 2) | | |
| JU1 / JU5 / JU3 / PTT | JU1 on, JU5 A, **JU3 L**, T | Same |
| TX pot (**R16**, JU3=L) | | |
| `-c left` on bench TX tests | default in `dra36m_check.py` | |
| IC-207H frequency / DATA | | |
| COS idle / busy bytes (`vol_dn`) | | |
| `--active-high` needed? | no / yes | |

---

## Sign-off (optional)

| Milestone | Date | Initials |
|-----------|------|----------|
| Mac + IC-2720: probe, monitor, smoke, voice | 2026-10-10 | |
| Mac + IC-2720: voice-gate (10 s clear) | 2026-10-10 | |
| Mac + IC-2720: retest `--with-voice --lead 0.6` | 2026-10-10 | |
| NUC + first DRA | | |
| NUC + dual DRA + 207H | | |
