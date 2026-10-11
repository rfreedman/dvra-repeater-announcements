# DRA-36M bench tests (`scripts/dra36m_check.py`)

Use these **before** wiring the announcements app to CM119A HID/PortAudio. Same commands work on **macOS (dev)** and **Linux (NUC)** except where noted.

Script path: [`scripts/dra36m_check.py`](../../scripts/dra36m_check.py) ([`scripts/README.md`](../../scripts/README.md)). Record results in [`bench-record.md`](bench-record.md).

---

## Prerequisites

| Item | Requirement |
|------|-------------|
| Python venv | `pip install -r requirements.txt` plus **`pip install hidapi`** |
| macOS | `brew install hidapi portaudio`; often `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` |
| Linux | `sudo apt install libportaudio2`; udev/`hidraw` access on NUC |
| DRA jumpers | **JU1 on**, **JU5 = A (1200)**, **JU3 = L** on both production DRA (must match `-c`; default **`left`**) |
| Radio | **1200 bps DATA**, speaker volume up, **mic unplugged** for on-air TX tests |
| RF safety | Low power, dummy load, or quiet simplex |

**Blue COMM OK** is a host-audio watchdog, not COS. **Bare `ptt` does not key RF** without USB streaming; use **`smoke`** or **`voice`** for real PTT.

---

## Recommended order (single radio)

Run from repo root with venv active.

| Step | Command | Pass criteria |
|------|---------|----------------|
| 1 USB | `probe` | Audio + HID OK; idle **`vol_dn busy: False`** when squelched |
| 2 COS live | `monitor --interval 0.5 --changes-only --cos vol_dn` | **busy** while other station TX; **idle** when quiet |
| 3 RF PTT + tone | `smoke --seconds 2 --lead 0.6` (or `-c left`) | TX after ~lead silence; tone clean (adjust **R16** with JU3=L) |
| 4 Piper on air | `voice -c left --lead 0.6 "…"` | Normal speed/intelligibility; first word not clipped |
| 5 Busy gate | `voice-gate -c left --poll 0.25 --clear-seconds 10 --lead 0.6` | No TX while other key held; one TX after full clear window |
| 6 Regression | `retest -c left --with-voice --lead 0.6` | Exit 0; voice sounds normal |

Optional: **`snap --period 0.25`** during step 2 for a burst log of `vol_dn` / `ir0..3`.

---

## macOS shell prefix

```bash
cd /path/to/dvra-repeater-announcements
source .venv/bin/activate
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib   # if needed
```

---

## Subcommands

### `probe`

Lists PortAudio DRA device and reads one idle HID sample.

```bash
python scripts/dra36m_check.py probe
```

**Pass:** `OK` for audio and HID; squelched **`vol_dn busy: False`**. If idle/busy labels swap, use `--active-high` on monitor/voice-gate later (not on probe).

---

### `monitor`

Polls COS on an interval (production-style **`vol_dn`** default).

```bash
python scripts/dra36m_check.py monitor --interval 1 --raw --cos vol_dn
python scripts/dra36m_check.py monitor --interval 0.5 --changes-only --cos vol_dn
```

If the channel is **already busy** at startup and HID enumerate fails, the script **retries up to 20s** (`--wait-device`). You can start monitoring during an ongoing QSO once the device appears.

| Flag | Meaning |
|------|---------|
| `--interval SEC` | Seconds between polls (default **1**) |
| `--cos vol_dn` | Preferred; **`allstar3`** cross-check |
| `--raw` | Print `ir0..3` and bit decode |
| `--changes-only` | Log only when state/bytes change |
| `--active-high` | Flip polarity if idle/busy swap |
| `--wait-device SEC` | Retry HID enumerate at startup (default **20**) |

**Pass:** Labels match what you hear on the monitored radio. Ctrl+C to stop.

**macOS:** While squelch is open, HID may stall — **`monitor --cos vol_dn`** reports **busy (HID read stalled)**. The monitor uses a **fresh HID open/read/close on each poll** (subprocess fallback if needed) so **idle** should return after unkey in the same session. Authoritative COS for production is still **NUC/Linux**.

Legacy **`dra_mac`** / **`audio_mac`** / **`hybrid_mac`**: not recommended on DRA-36M after **JU1/JU5** fix; kept for old benches.

---

### `snap`

Rapid COS samples (good while someone else keys).

```bash
python scripts/dra36m_check.py snap --period 0.25 --count 40
```

| Flag | Meaning |
|------|---------|
| `--period SEC` | Delay between samples (default **0.25**) |
| `--count N` | Number of lines (default **30**) |
| `--active-high` | Flip **`vol_dn`** polarity |

**Pass:** `vol_dn` toggles with over-the-air activity.

---

### `ptt`

HID GPIO3 only — **no USB audio**.

```bash
python scripts/dra36m_check.py ptt --seconds 2
```

**Expect on DRA-36M:** Often **no RF** (COMM OK watchdog). Used in **`retest`** as a quick HID write check only. **Do not** treat as RF pass/fail.

---

### `tone`

Test tone to DRA USB **without** PTT (level/deviation check).

```bash
python scripts/dra36m_check.py tone -c left --seconds 4 --level 0.3
```

| Flag | Meaning |
|------|---------|
| `-c left\|right\|mono` | Must match **JU3** (production: **`left`**, default) |
| `--level` | Sine amplitude (default **0.25**); Piper **`voice`** is much hotter—trim **R16** (JU3=L) for speech |

---

### `smoke`

PTT + **lead silence on USB** + tone + unkey (COMM OK + radio settle).

```bash
python scripts/dra36m_check.py smoke -c left --seconds 4 --lead 0.6 --level 0.3
```

| Flag | Meaning |
|------|---------|
| `--lead SEC` | **Silence on air** after PTT before tone (default **0.4**) |
| `--seconds` | Tone duration |
| `--frequency` | Hz (default **800**) |
| `--level` | Tone level (default **0.3**) |

**Pass:** RF keys; quiet period then tone; not fuzzy (adjust TX pot).

---

### `voice`

PTT + lead silence + **Piper** TTS on air (22050 Hz stream rate, not 48 kHz device default).

```bash
python scripts/dra36m_check.py voice -c left --lead 0.6 "This is a test."
python scripts/dra36m_check.py voice -c left --lead 0.6 \
  "This is w2-zee-q. [pause:2] Testing pause tags."
```

| Flag | Meaning |
|------|---------|
| `--lead SEC` | Silence after PTT before speech (default **0.4**; **0.6** typical on bench) |
| `-v VOICE` | Piper voice id/alias |
| Text | Words as arguments; default sentence if omitted |

**Pass:** Normal speech rate; clear with **R16** set; pauses honored; opening not clipped (increase **`--lead`** if needed).

---

### `voice-gate`

Simulates **defer while busy** + **clear window** before one announcement (app `channel_busy()` behavior).

```bash
python scripts/dra36m_check.py voice-gate -c left --poll 0.25 --lead 0.6 \
  --clear-seconds 10
```

**Procedure**

1. Start script (monitored rig squelched).
2. Key **another** radio and **hold** → logs **`COS busy — not transmitting`**, no RF from script.
3. **Unkey** → logs **`need 10s continuous clear before TX`** (or your **`--clear-seconds`**).
4. If someone keys again during clear → **`reset clear wait`**.
5. After **full clear window** → **one** Piper TX and exit.

| Flag | Meaning |
|------|---------|
| `--poll SEC` | COS poll interval (default **0.25**; same role as **`snap --period`**) |
| `--clear-seconds SEC` | Continuous idle required before TX (default **10**) |
| `--wait-busy SEC` | Max wait for you to key other radio (default **120**) |
| `--wait-idle SEC` | Max wait after busy for clear + TX (default **300**) |
| `--min-busy SEC` | Ignore brief idle blips during hold (default **0.5**) |
| `--lead SEC` | Pre-speech silence after PTT (default **0.6**) |
| `--cos` | **`vol_dn`** (default), **`allstar3`**, or legacy **`dra_mac`** |
| `--active-high` | Flip **`vol_dn`** polarity |
| `--wait-device SEC` | Retry HID enumerate at startup (default **20**) |

**Pass:** No TX during hold; no TX during clear window if channel busy again; exactly one TX after stable clear.

**macOS:** COS polling uses one-shot HID reads. Piper is **not** synthesized during the wait; at TX the script runs **`voice`** in a **subprocess** (avoids PortAudio `-9986` after HID polling).

---

### `retest`

Automated bundle: **`probe`** → COS idle sample → **`ptt` 0.5s** → **`smoke`** → optional **`voice`**.

```bash
python scripts/dra36m_check.py retest -c left --with-voice --lead 0.6
```

| Flag | Meaning |
|------|---------|
| `--with-voice` | Run Piper at end |
| `--voice-text` | String for voice step |
| `--smoke-seconds` | Tone length (default **2**) |
| `--lead` | Used for smoke + voice |

**Pass:** Exit 0; voice intelligible.

---

### `hid-once` (internal)

Hidden helper: one HID read as JSON. Used by macOS COS polling fallback; not part of normal manual checkout.

---

## Flag cheat sheet

| Flag | Used on | Purpose |
|------|---------|---------|
| **`--lead`** | `smoke`, `voice`, `voice-gate`, `retest` | Silence on USB/air after PTT before program audio |
| **`--poll`** | `voice-gate` | COS sample period |
| **`--period`** | `snap` | Time between COS samples |
| **`--interval`** | `monitor` | Time between COS polls |
| **`--reacquire-after`** | `monitor` | Legacy: persistent-handle reopen (default **0**; macOS uses one-shot reads instead) |
| **`--wait-device`** | `monitor`, `voice-gate` | Startup HID enumerate retry (default **20** s) |
| **`--clear-seconds`** | `voice-gate` | Idle hang time before TX (default **10**) |
| **`-c` / `--channel`** | TX commands | **`left`** (production default) / **`right`** must match **JU3** |

---

## NUC notes

- Omit **`DYLD_*`** on Linux.
- Prefer **`monitor --cos vol_dn`** as authoritative COS polarity for future app config.
- Dual DRA: run this checklist **per radio** before dual-TX software exists.

---

## References

- Hardware jumpers/checkout: [`README.md`](README.md)
- Your recorded values: [`bench-record.md`](bench-record.md)
