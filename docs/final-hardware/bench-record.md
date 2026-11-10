# Bench record (DRA + radio checkout)

A **DRA** (Digital Radio Adapter) is the USB box between the computer and one radio. This project uses the [Masters Communications **DRA-36M**](https://www.masterscommunications.com/products/radio-adapter/dra/dra36m.html). It plays the announcement into the radio and keys the transmitter. The setting `TTS_RADIO_BACKEND=dra` means the app is using those adapters.

**Piper** is a text-to-speech (TTS) library. It turns announcement text into the speech the radio transmits.

Recorded on the Linux host used for radio development (**Ubuntu 26.04.1 LTS**). Radios have not been tried on macOS. **Production (both radios): JU3 = L, USB left channel, R16.**

**Linux host (2026-10-24):** Single-DRA **`retest`** **pass** with **IC-2720** (dev/test rig) and with production **IC-207H**. Dual-DRA **`dual-retest`** **pass** with **IC-2720** + **FT-8900R** (2× DRA-36M). The announcements app (`TTS_RADIO_BACKEND=dra`) has been run on this host with two DRAs and transmitted on both radios. **Linux host + 2× IC-207H** dual checkout is not recorded yet.

**How to run each test:** [`dra36m-check-tests.md`](dra36m-check-tests.md) (probe, monitor, snap, smoke, voice, voice-gate, retest, flags).

---

## DRA-36M (unit under test)

| Item | Linux host value | Notes |
|------|------------------|--------|
| **JU1** | Installed | Factory = off |
| **JU5** | **A (1200)** | Factory = B (9600) |
| **JU3** | **L (left)** | Pin 1 ← USB left channel |
| **PTT pair** | **T** (transistor) | Switch to **R** if a radio will not key |
| **JU4** | Off (default) | — |
| **TX level pot** | **R16** (left / JU3=L) | Backed off from factory until Piper voice was clear (no fuzzy over-deviation). *Record your panel mark:* _________________ |
| **USB** | Direct motherboard ports (no unpowered hub) | |
| **Blue COMM OK** | Off during HID-only tests | Normal; streams during `smoke` / `voice` |

---

## Software / COS

| Item | Value |
|------|--------|
| **COS mode** | `monitor --cos vol_dn` |
| **Polarity** | Default active-low — **no** `--active-high` |
| **Idle HID (squelched)** | `ir0..3=02000000`, `vol_dn busy: False` |
| **Cross-check** | `allstar3` matched `vol_dn` in testing |
| **Bare `ptt` subcommand** | Does not key RF (no USB audio → COMM OK watchdog) |
| **RF PTT tests** | Use `smoke` or `voice` (audio + GPIO3) |

---

## Software / transmit

| Item | Value |
|------|--------|
| **USB channel flag** | `-c left` (matches **JU3 = L**) |
| **PTT lead** | `--lead 0.6` (silence on air before speech; tune 0.5–0.8 if needed) |
| **Piper voice** | `en_US-ryan-medium` @ **22050 Hz** (stream at Piper rate, not 48 kHz device default) |
| **Regression** | `python scripts/dra36m_check.py retest -c left --with-voice --lead 0.6` |
| **Busy gate** | `voice-gate -c left --poll 0.25 --clear-seconds 10 --lead 0.6` |

---

## IC-2720 (dev bench radio)

| Item | Value | Your notes |
|------|--------|------------|
| **Role** | Dev/test rig on the **Linux host** — single-radio `retest` and one leg of **dual** (with FT-8900R) | |
| **Frequency** | 146.520 MHz simplex (2 m calling) | |
| **DATA rate** | **1200 bps** (not 9600) | |
| **Band / mode** | | e.g. VFO, packet/DATA band as set on rig |
| **SQL (P SQL)** | | Knob position when COS tracked reliably |
| **Speaker volume** | Up (pin 6 / COS) | |
| **Mic** | Unplug for on-air `voice` tests @ 1200 | |

---

## IC-207H (production radio)

| Item | Value | Your notes |
|------|--------|------------|
| **Role** | Production repeater radio; **single-radio** `retest` passed on the Linux host (2026-10-24) | |
| **DATA rate** | **1200 bps** (not 9600) | Same Mini-DIN DATA jack as IC-2720 |
| **Frequency / DATA** | | |
| **SQL (SQ)** | | Knob up enough for pin 6 COS |
| **Mic** | Unplug for on-air tests @ 1200 | |

---

## FT-8900R (Linux bench, dual-radio second rig)

| Item | Value | Your notes |
|------|--------|------------|
| **Role** | Second radio for **dual DRA** checkout on the **Linux host** (IC-2720 on the other DRA) | |
| **Regression** | `dual-retest -c left --with-voice --lead 0.6` with both DRAs on the Linux host | **Pass** 2026-10-24 |
| **DATA / packet** | | 1200 bps DATA as used for checkout |
| **Mic / volume** | | As set when COS and TX passed |

---

## Linux host + two IC-207H (optional detail)

| Item | Radio 1 | Radio 2 |
|------|---------|---------|
| Linux host USB port (fixed) | | |
| udev rules + user in `plugdev` (see [dra36m-check-tests.md](dra36m-check-tests.md#linux-udev-and-plugdev)) | | |
| ALSA / PortAudio name | | |
| hidraw + `TTS_DRA_HID_PATH` if pinned | | |
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
| Linux host + IC-2720: single-radio `retest --with-voice --lead 0.6` | 2026-10-24 | |
| Linux host + IC-207H: single-radio `retest --with-voice --lead 0.6` | 2026-10-24 | |
| Linux host + IC-2720 + FT-8900R: dual `dual-retest --with-voice --lead 0.6` | 2026-10-24 | |
| Linux host + 2× IC-207H: dual `dual-retest --with-voice` | | |
