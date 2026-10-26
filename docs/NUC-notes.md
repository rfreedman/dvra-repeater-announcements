# NUC production notes (shack)

Operational lessons from **Xubuntu on the Intel NUC** + **DRA-36M** bench checkout (2026-10). On the NUC (2026-10-24): **single-radio** `retest` with **IC-2720** and **IC-207H**; **dual DRA** `dual-retest` with **IC-2720** + **FT-8900R**. Re-run `probe-all` / `dual-retest` when both production **IC-207H** replace the bench pair. For jumpers, checkout order, and `dra36m_check.py` commands, see [`final-hardware/`](final-hardware/README.md).

After **deploy or restart**, busy-retry / defer state and login lockout counters are in-memory only — see [README — Process restart and in-memory state](../README.md#process-restart-and-in-memory-state).

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

The announcements app plays to the DRAs with **`aplay -D plughw:N,0`**, not the desktop default sink. KDE / PipeWire can still stream **UI sounds** to a C-Media USB sink and light the DRA front panel even when **`python -m app serve` is idle** and not keying PTT.

### DRA LEDs (idle vs streaming)

| LED | Normal when idle | During host USB audio |
|-----|------------------|------------------------|
| Green (heartbeat) | Solid | Blinking |
| Blue (COMM OK) | Off | Solid |
| Red (PTT) | Off | On only while keyed |

See [DRA-36M status LEDs](final-hardware/README.md#dra-36m-status-leds-masters). Busy / COS does **not** drive blue.

### Fix that worked on shack-nuc (2026-10)

**Symptom:** One DRA showed **green blinking + blue solid** with both units connected and no transmit. **`fuser`** showed **`pipewire`** on **`/dev/snd/pcmC*D0p`**. **`wpctl inspect`** on the **libcanberra** stream reported **`event.id = "audio-volume-change"`** and **`target.object`** pinned to a **C-Media Pulse sink** (not the default null sink). **`kdeglobals` `[Sounds] Enable=false`** alone did **not** stop the loop; **`kcm_soundrc`** volume feedback did.

**Persistent KDE settings** (survived reboot; both DRAs **green solid / blue off** after restart):

```bash
kwriteconfig6 --file kdeglobals --group Sounds --key Enable false
kwriteconfig6 --file kcm_soundrc --group General --key EnableSoundFeedback false
killall kded6   # once, after writing config; Plasma respawns kded
```

Optional in **System Settings → Sound**: default playback **not** “USB PnP Sound Device”; turn off event / feedback sounds if the GUI exposes them.

**Optional session helpers** (the null sink module is **not** kept across PipeWire restart unless you add a login script):

```bash
pactl load-module module-null-sink sink_name=shack_null sink_properties=device.description=Shack_Null_Output
pactl set-default-sink shack_null
# Pulse index from first column of `pactl list sinks short` — names vary by USB path:
pactl suspend-sink alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-stereo 1
pactl suspend-sink alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.2.analog-stereo 1
```

On this NUC, built-in **HDA Intel PCH** had **`Active Profile: off`** in PipeWire (no HDMI monitor / no analog jack), so **`pactl list sinks short | grep -v C-Media`** was empty until a profile or null sink was added. That made it easy for the default to land on a DRA.

### If it happens again (runbook)

Work on the NUC console; one check at a time is fine.

1. **Confirm the app is not transmitting** — no stuck **`aplay`** / serve log in a TX phase; red PTT LED off on the DRA.
2. **See who holds ALSA playback:** `fuser -v /dev/snd/pcmC0D0p /dev/snd/pcmC1D0p 2>&1` — if **`pipewire`**, treat as desktop audio, not scheduler TX.
3. **Find the stream:** `wpctl status` → **Audio → Streams**. If **libcanberra** / **kded6** links to **USB PnP Sound Device** playback ports, inspect it: `wpctl inspect <stream-id>` and look for **`audio-volume-change`** / **`target.object`**.
4. **Pulse view:** `pactl list sinks short` — a **RUNNING** **`alsa_output.usb-C-Media_…`** sink while idle is a smoking gun. `pactl get-default-sink` should not be a C-Media device for desktop use.
5. **Apply the fix:** ensure **`EnableSoundFeedback false`** in **`~/.config/kcm_soundrc`** and **`Sounds` → `Enable=false`** in **`~/.config/kdeglobals`**, then **`killall kded6`**. Re-check **`wpctl status`** (no libcanberra stream) and DRA LEDs.
6. **If LEDs stay wrong but `fuser` is empty** — **unplug/replug** that DRA’s USB cable to reset the adapter watchdog (hardware latch).
7. **After PipeWire restart / reboot** — re-create **`shack_null`** and suspend both C-Media sinks if you use that pattern; confirm settings in step 5 still present (`kreadconfig6 --file kcm_soundrc --group General --key EnableSoundFeedback` → **`false`**).

**Less reliable on PipeWire-pulse:** `pactl move-sink-input`, `pactl kill-sink-input` (often missing), `wpctl kill` (not in WirePlumber 0.5.x). Suspending the **Pulse sink index** from **`pactl list sinks short`** (e.g. `pactl suspend-sink 73 1`) can idle the sink but may not drop a **stuck libcanberra loop** — disable volume feedback (step 5) instead.

Shorter notes: **plasmashell** / **libcanberra** after bench tests can leave COMM OK / HB active; default sink away from USB plus **EnableSoundFeedback false** prevents recurrence. Do not rely on **`sudo`** for bench tests — use udev + **`plugdev`** instead.

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
- If HID count ≠ audio count, run `python scripts/dra36m_check.py probe-all`, confirm udev/`plugdev`, and see [Identifying two identical adapters](final-hardware/README.md#identifying-two-identical-adapters) in [`final-hardware/README.md`](final-hardware/README.md).
- With both DRAs connected: `probe-all` then `dual-retest -c left --with-voice --lead 0.6` ([dual checklist](final-hardware/dra36m-check-tests.md#dual-dra-two-adapters-on-one-host)). For first-time checkout of one new unit, unplug the other and use single-radio `retest`.

---

## App on the NUC

- Set **`TTS_RADIO_BACKEND=dra`** in `.env` for on-air scheduled playback (keep **`stub`** on dev Macs without hardware).
- After a clean start, check the log for **`Startup complete … (radio mode: dra)`** (console or `data/logs/announcements.log`). If you still see **`stub`**, the process registered the in-memory backend—fix `.env` and restart. If startup exits before that line, DRA init failed (HID, udev, or audio device).
- Record rig-specific values in [`final-hardware/bench-record.md`](final-hardware/bench-record.md).
