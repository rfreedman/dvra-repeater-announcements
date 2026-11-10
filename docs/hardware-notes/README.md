# Hardware notes

| Document | Contents |
|----------|----------|
| [connectors.md](connectors.md) | IC-207H / IC-2720 Mini-DIN DATA jack (pins, PTT, SQL) |
| [linux-host.md](../linux-host.md) | Linux host: udev, ALSA mixer, PipeWire, DRA bench gotchas |

A **DRA** (Digital Radio Adapter) is the USB box between the computer and one radio. This project uses the [Masters Communications **DRA-36M**](https://www.masterscommunications.com/products/radio-adapter/dra/dra36m.html). It plays the announcement into the radio and keys the transmitter. The setting `TTS_RADIO_BACKEND=dra` means the app is using those adapters.

Checkout and bench tests: [`../final-hardware/README.md`](../final-hardware/README.md).
