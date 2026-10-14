# NUC production notes (shack)

Operational lessons from **Xubuntu on the Intel NUC** + **DRA-36M** bench checkout (2026-10). For jumpers, checkout order, and `dra36m_check.py` commands, see [`final-hardware/`](final-hardware/README.md).

---

## Permissions (CM119 USB + HID)

Install udev rules and add the runtime user to **`plugdev`**. Full steps:

[`final-hardware/dra36m-check-tests.md` — Linux: udev and plugdev](final-hardware/dra36m-check-tests.md#linux-nuc-udev-and-plugdev)

Run bench scripts and `python -m app serve` as that user — **not** `sudo` (root has no PulseAudio session; PortAudio can fail with `PaErrorCode -9999`).

---

## ALSA mixer: unmute USB Speaker

After plug-in or reboot, the CM119 USB card often comes up with **Speaker muted and 0%**. Symptom: **PTT keys** (HID OK) but **no modulation** on the air (smoke tone and Piper both silent).

Check (adjust card number if `aplay -l` differs):

```bash
amixer -c 1 scontents
amixer -c 1 sset Speaker 100% unmute
```

Re-run `python scripts/dra36m_check.py smoke -c left --seconds 2 --lead 0.6` to confirm tone on DATA IN. Adjust **R16** on the DRA (JU3=L) for deviation, not only software volume.

With `TTS_RADIO_BACKEND=dra`, the app **unmutes Speaker at startup** on each CM119 ALSA card it uses (see `TTS_DRA_ALSA_UNMUTE`, `TTS_DRA_ALSA_SPEAKER_LEVEL`). Startup fails if `amixer` cannot unmute — fix permissions or install `alsa-utils`.

After **USB replug** while the app is running, the card often mutes again; the app watches `/proc/asound/cards` on the COS poll (~0.25 s) and before transmit, and re-runs `amixer` when CM119 card numbers change (no app restart needed).

Optional: udev `@reboot` / RUN rules if you want unmute outside the app.

---

## Piper sample rate vs CM119

Piper outputs **22050 Hz**; the DRA USB interface accepts **44100 / 48000 Hz**. The app and bench scripts **resample to the device’s PortAudio default** (usually 44100). Log line example: `22050 Hz → 44100 Hz USB`.

---

## Desktop audio (KDE / PipeWire)

Keep the **default Pulse/PipeWire output** on **HDMI or onboard**, not the C-Media USB device:

```bash
pactl set-default-sink alsa_output.pci-0000_00_1f.3.hdmi-stereo   # example; use `pactl list sinks short`
```

Otherwise **plasmashell** / **libcanberra** (volume click sounds) can target the DRA sink, leaving **COMM OK** on and **HB** blinking after tests even when the app has stopped. If that happens:

1. Set default sink away from USB, kill stray sink-inputs if needed, or  
2. **Unplug/replug** the DRA USB cable to reset the adapter LEDs.

Do not rely on `sudo` for bench tests to “fix” permissions — use udev + `plugdev` instead.

---

## Optional single-DRA pin

When only one DRA is connected, `probe` prints a path for the app:

```text
TTS_DRA_HID_PATH=<hex from probe>
```

Example from one shack NUC: `312d343a312e33` (USB path `1-4:1.3`).

## Two DRAs on one NUC

Dual transmit uses **two separate CM119A devices** (one per DRA-36M): two USB audio endpoints and **two** HID interfaces. At startup the app should log **2 HID + 2 PortAudio** pairings.

- Leave **`TTS_DRA_HID_PATH` unset** — it selects a single CM119 and hides the second radio’s PTT/COS.
- Use **fixed USB ports** on the NUC; optional **`TTS_DRA_AUDIO_DEVICES`** if device order varies.
- With PipeWire, the same CM119 may appear as both `(hw:…)` and `alsa_output.usb-…` in PortAudio. The app defaults to **`(hw:…)`** for device pick (PipeWire sink playback can hang PortAudio). Set **`TTS_DRA_PREFER_ALSA_HW=0`** to use the Pulse sink. **Playback on Linux uses `aplay -D plughw:N,0` by default** (`TTS_DRA_LINUX_PLAYBACK=aplay`); set **`TTS_DRA_LINUX_PLAYBACK=portaudio`** to force PortAudio. Unmute uses **all playback ALSA controls** on the card (not only `Speaker`).
- If HID count ≠ audio count, run `python scripts/dra36m_check.py probe`, confirm udev/`plugdev`, and see [Identifying two identical adapters](final-hardware/README.md#identifying-two-identical-adapters) in [`final-hardware/README.md`](final-hardware/README.md).

---

## App on the NUC

- Set **`TTS_RADIO_BACKEND=dra`** in `.env` for on-air scheduled playback (keep **`stub`** on dev Macs without hardware).
- After a clean start, check the log for **`Startup complete … (radio mode: dra)`** (console or `data/logs/announcements.log`). If you still see **`stub`**, the process registered the in-memory backend—fix `.env` and restart. If startup exits before that line, DRA init failed (HID, udev, or audio device).
- Record rig-specific values in [`final-hardware/bench-record.md`](final-hardware/bench-record.md).
