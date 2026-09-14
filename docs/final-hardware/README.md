# Final hardware: Pi 4B (2Mb) + two DRA-36M Digital Radio Adapters

This is the production radio interface for the announcements app.

**Decision:** two assembled Masters [DRA-36M](https://www.masterscommunications.com/products/radio-adapter/dra/dra36m.html) units, each with its own USB cable to the Raspberry Pi 4 and its own Mini-DIN-6 cable to one radio. Both radios hear the same announcement, key together, and report busy independently.

## Parts List / Cost

| Item | <div style="white-space: nowrap;">Cost Ea</div> | <div style="white-space: nowrap;">Qty</dvi>  | <div style="white-space: nowrap;">Total</div> | Source |
| ---- | ------- | ---- | ---- | ---- |
|DRA-36M + cable & shipping|$129|2|$258|masterscommunications.com|
||
|Pi4 4Gb| $120| 1| $120 | amazon.com/Raspberry-Pi-RPI4-MODBP-4GB-Model-4GB/dp/B09TTNF8BT|
|Passive Aluminum Case|$10|1|$10|amazon.com/Geekworm-Raspberry-Compatible-Aluminum-Only-Black/dp/B07ZVJDRF3|
|Power Supply w/switch|$10|1|$10|amazon.com/GeeekPi-Supply-Raspberry-Orange-Adapter/dp/B0BMGJNSVS|
|Tax @ Amazon| $10 | 1 | $10||
||
| SD Card | $0 | 2 | $0 | have leftover from hamclock project |
||
|**TOTAL**|||**$408**|

**Cost w/ 1 DRA-45 is ~ $270**

**Cost w/ no DRA is ~ $150**



## Station

| Role | Choice |
|---|---|
| Host | Raspberry Pi 4, 64-bit Raspberry Pi OS |
| Production radios | **IC-207H** |
| Test radio | **IC-2720** (same DATA jack; development only) |
| Radio interface | 2 × DRA-36M, assembled, metal case |
| Audio path | USB only. The Pi headphone jack is unused. |
| PTT / busy | Each DRA’s CM119A HID (GPIO3 PTT, COS from SQL) |
| Storage | Quality A2 microSD or USB SSD |
| Power | Official-class 5 V / 3 A USB-C supply |

The IC-207H and IC-2720 share the rear 6-pin Mini-DIN DATA jack, ground-to-key PTT, and pin 6 SQL (high when squelch is open). SQL is labeled **SQ** on the 207H and **P SQL** on the 2720.

---

## Buy list

From [Masters Communications](https://www.masterscommunications.com/products/radio-adapter/dra/dra36m.html) (list prices, shipping extra):

| Qty | Item | Notes | List |
|---|---|---|---|
| 2 | DRA-36M assembled and tested, metal case | Not the kit, not the non-M DRA-36 | $101 each |
| 2 | DIN6-Shortie (3 ft) **or** DRAC-12 (6 ft) | Radio cables are **not** in the box | $10 / $12 each |
| 2 | USB-A to USB-B cable | DRA rear jack is USB-B; confirm whether a cable ships with the unit | — |

Typical total with two Shorties: about **$222** plus USB cables and shipping.

Plug both DRA USB cables **directly into the Pi 4**. Do not put them on an unpowered hub.

Label the cases **Radio 1** and **Radio 2** and keep each DRA on a dedicated USB port so udev can bind by path.

**Not required:** custom GPIO board, Y-cable, DRA-Switch, DRA-3M mixer, extra PTT transistor, analog audio cable from the Pi.

---

## How audio actually moves

The DRA-36M **is** the sound card. Piper / PortAudio play into that USB device. The CM119A DAC creates analog TX audio. The DRA sends it out Mini-DIN **pin 1**, which is the radio’s **DATA IN**.

```
Pi app  --USB-->  DRA-36M  --Mini-DIN pin 1-->  radio DATA IN
                   CM119A  --Mini-DIN pin 3-->  radio PTT (ground to key)
                           --Mini-DIN pin 6-->  radio SQL (COS)
                           --Mini-DIN pin 2-->  ground
```

There is no analog lead from the Pi into a “DRA DATA IN.” DATA IN is on the radio. The stock Mini-DIN cable already makes that connection.

On each DRA, **JU3** chooses whether pin 1 is fed from the USB **left** or **right** channel. Play (or upmix) onto that same channel. TX level is the DRA’s transmit pot for that channel (R14 = right, R16 = left), not the Pi headphone volume.

This app does not use receive audio from the radio (pins 4 / 5). Busy is taken from SQL on pin 6.

---

## Mini-DIN-6 (radio DATA jack and DRA)

Stock Masters DIN6 cables are 1:1. Both ends use this pinout.

| Pin | Radio (IC-207H / IC-2720) | DRA-36M |
|---|---|---|
| 1 | Data In (1200 bps path) | TX audio (JU3 left or right) |
| 2 | Ground | Ground |
| 3 | PTT (pull to ground to key) | PTT (transistor or relay) |
| 4 | Data Out | 9600 RX audio (unused here) |
| 5 | AF Out ~270 mV p-p | 1200 RX audio (unused here) |
| 6 | SQL — **high when squelch is open** | COS (AllStar HID input when **JU1** is installed) |

Leave pins 4 and 5 unwired in software. Do not use the radio’s 9600 bps DATA setting; that path bypasses the limiter.

---

## Jumper settings (same on both DRA-36M)

Masters default is digital-mode (VARA / Direwolf): **JU1 off**, JU3 = R, JU5 = B. **This app is not that.** COS must be enabled so the Pi can see SQL.

The 36M has **no JU2** (CTCSS input was omitted). All other jumper numbers match the DRA-36.

| Jumper | Setting for this app | Why |
|---|---|---|
| **JU1** | **Installed** | Enables COS from Mini-DIN pin 6 (radio SQL) as the CM119A HID bit |
| JU2 | Not present on 36M | — |
| **JU3** | **R** on both units (Masters default) | Pin 1 = USB right channel. Keep both DRA identical so software can play the same stream |
| JU4 | Leave off unless the PTT LED glows with the radio off | Anti-false PTT LED; Icom base radios usually leave this off |
| JU5 | Don’t care | Selects RX audio pin 4 vs 5; this app does not record from the radio |
| **PTT pair** | Start on **T** (transistor). Both shunts required, parallel to the silkscreen arrows | Icom packet PTT is ground-to-key. If a radio will not key, rotate both shunts 90° to **R** (reed relay). The 36M relay option exists for Icom radios that dislike open-collector keying |

After JU3 = R, set TX deviation with **R14** (right-channel pot) on each DRA. If you ever move JU3 to L, use **R16** instead.

R12 is receive level and is irrelevant until something reads RX audio.

Official jumper text: [dra36-jumpers.txt](https://masterscommunications.com/products/radio-adapter/dra/txt/dra36-jumpers.txt). Pinout: [dra36-DIN-pinout.txt](https://masterscommunications.com/products/radio-adapter/dra/txt/dra36-DIN-pinout.txt). 36 vs 36M: [dra36-vs-dra36m](https://www.masterscommunications.com/products/radio-adapter/dra/dra36-vs-dra36m.html).

---

## Radio settings (both IC-207H and IC-2720)

1. DATA speed **1200 bps**, not 9600. 9600 bypasses the limiter and is the wrong audio injection point.
2. At 1200 bps, **unplug the microphone** while the app is transmitting. Otherwise mic audio is mixed onto the announcement. (9600 mutes the mic automatically; do not switch to 9600 to get that mute.)
3. Keep the radio’s speaker volume at a **normal** level. Pin 6 SQL may not assert if the volume is at zero.
4. SQL must actually open for pin 6 to go high. If a tone squelch is on, also leave carrier squelch at a usable setting so SQL follows the channel.
5. Confirm PTT: Mini-DIN pin 3 to ground keys the transmitter.

Ignore IC-2720 packet-band lock for this project. Production is the IC-207H.

---

## PTT and busy (what the app must do)

Announcements go out on **both** radios at once. Never talk on one machine while the other has a QSO.

| Function | Hardware | Software |
|---|---|---|
| Busy | Each DRA COS (HID), from that radio’s pin 6 SQL | `channel_busy()` is true if **either** COS is active. Defer with existing `TTS_BUSY_RETRY_SECONDS` / `TTS_BUSY_GIVE_UP_SECONDS` until **both** are clear |
| PTT | Each DRA CM119A **GPIO3** | `set_ptt(True)` asserts **both** HID PTT bits. Keep `TTS_PTT_LEAD_SECONDS` (default 0.4 s) before audio. Release both in `finally` |
| Audio | Two PortAudio (CM119A) devices | Play the **same** PCM on **both** devices, or a PulseAudio / PipeWire combine sink that feeds both. Piper is mono; upmix or duplicate as needed so the JU3 channel has signal |

SQL polarity: radio pin 6 is **high when the squelch is open**. CM119A COS for AllStar is normally **active-low**. Invert in software after measuring, or use any NPN inverter Masters documents for that COS input. Confirm with a DMM on pin 6 (squelched vs open) and on the HID bit before relying on busy detect.

Current code: [`transmit()`](../../app/radio.py) already keys, waits the lead, plays, then unkeys. [`play_chunks`](../../app/playback.py) opens the **default** PortAudio device only. Replacing `StubRadio` and teaching playback about two devices is still future software work.

---

## Identifying two identical adapters

Both DRA-36M units enumerate as the same C-Media CM119A (USB sound + HID). Enumeration order is not stable across reboots.

1. Plug Radio 1’s DRA into a chosen Pi USB port and leave it there.
2. Same for Radio 2 on a different port.
3. udev rules keyed on **USB path** (`KERNELS=="1-1.x"`) or the device serial, not “first CM119A vs second.”
4. Grant the service user access to both `hidraw` nodes and both ALSA / PortAudio devices.

`lsusb` and `arecord -l` / `aplay -l` should show two C-Media devices. `ls /dev/hidraw*` should show two HID interfaces after the DRA are plugged in.

---

## Checkout (no app changes required)

Work one radio at a time, low power, dummy load or known quiet simplex.

1. Jumpers as in the table. JU1 on, JU3 = R, PTT = transistor to start.
2. USB into the Pi. Blue COMM OK LED on. `aplay -l` lists the CM119A.
3. Mini-DIN cable to the radio DATA jack. Radio on 1200 bps DATA, mic unplugged, volume up.
4. In `alsamixer` (that USB card), unmute the playback path and raise the PCM slider.
5. Play a test tone or `speaker-test` **to that USB device** (not `hw:Headphones`). Adjust R14 until DATA-IN deviation is reasonable. Repeat for the second DRA.
6. Key test: assert GPIO3 on that HID (or Masters’ documented PTT test). Radio should TX. If not, switch the PTT pair to relay.
7. Busy test: squelch closed → pin 6 low (or whatever the radio actually does) and COS HID idle. Open squelch with a signal or by turning SQL down → pin 6 high and COS HID changes. Record the polarity for software.
8. Repeat for the second DRA + second radio.
9. Confirm the Pi 3.5 mm jack is silent during these tests — if you hear the announcement there, playback is aimed at the wrong device.

---

## Host notes

- 64-bit Raspberry Pi OS (32-bit cannot run this Piper stack).
- `libportaudio2` on the Pi for playback.
- Boot from a quality A2 card or USB SSD so `announcements.json`, PCM cache, and logs are not on a cheap card.
- Official-class 5 V / 3 A USB-C supply. Two DRA-36M units are USB-powered; keep them on the Pi’s own ports.
- Pi GPIO header is unused. A NUC would work as the same USB host but is optional.

---

## Operator reminders

- Unplug mics at 1200 bps whenever the scheduler might TX.
- If pin 6 never shows busy, turn the radio volume up before chasing software.
- Same announcement on both machines; busy if either is busy; PTT both or neither.

---

## References

- DRA-36M product: https://www.masterscommunications.com/products/radio-adapter/dra/dra36m.html
- Support docs: https://masterscommunications.com/products/radio-adapter/dra/dra36m_docs.html
- Jumpers: https://masterscommunications.com/products/radio-adapter/dra/txt/dra36-jumpers.txt
- Mini-DIN pinout: https://masterscommunications.com/products/radio-adapter/dra/txt/dra36-DIN-pinout.txt
- 36 vs 36M: https://www.masterscommunications.com/products/radio-adapter/dra/dra36-vs-dra36m.html
- Earlier (superseded) notes: [`docs/hardware-notes/`](../hardware-notes/) — GPIO PCB, breadboard, DRA-45M Y-cable
- Radio DATA jack detail also in [`docs/hardware-notes/connectors.md`](../hardware-notes/connectors.md)
- Scheduling: [`docs/how-to-schedule.md`](../how-to-schedule.md)
