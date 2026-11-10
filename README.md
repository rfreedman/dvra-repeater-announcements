# DVRA Repeater Announcements

Station software for scheduled repeater announcements. **Piper** is a text-to-speech (TTS) library. It turns announcement text into the speech the radio transmits. The app also plays Amateur Radio Newsline audio. A FastAPI web UI manages the announcement library and schedules. A **DRA** (Digital Radio Adapter) is the USB box between the computer and one radio. This project uses the [Masters Communications **DRA-36M**](https://www.masterscommunications.com/products/radio-adapter/dra/dra36m.html). It plays the announcement into the radio and keys the transmitter. The setting `TTS_RADIO_BACKEND=dra` means the app is using those adapters. Audio is generated and played **in memory** — nothing is written as WAV/MP3 for on-air use.

Development and testing **without radios** is done on macOS. That work was done on a MacBook Pro M3 running macOS Tahoe. Radios have not been tried on macOS; use `TTS_RADIO_BACKEND=stub` there.

Development and production **with radios** run on a 64-bit Linux computer. That work has been done only on Ubuntu 26.04.1 LTS, including two DRAs transmitting together. Use `TTS_RADIO_BACKEND=dra` on the Linux host.

## Requirements

- macOS (no radios) or **64-bit Linux** (with radios). Hardware: [docs/final-hardware/README.md](docs/final-hardware/README.md). DRA bench: [dra36m-check-tests.md](docs/final-hardware/dra36m-check-tests.md), [scripts/README.md](scripts/README.md)
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- PortAudio for CLI/local playback (`brew install portaudio` / `sudo apt install libportaudio2`)

## Setup

```bash
uv sync
source .venv/bin/activate   # optional; or use uv run …
```

Dependencies live in `pyproject.toml` + `uv.lock` (commit both). Use `uv add` / `uv remove` — do not `pip install` into the venv.

**Deploy to the Linux host:** [docs/deploy.md](docs/deploy.md) (`scripts/deploy`, `serve.sh`, version bumps, restart behavior). The default SSH hostname `repeater-nuc` is this station’s machine, not a hardware requirement.

**Environment variables:** [docs/env-reference.md](docs/env-reference.md) and [`.env.example`](.env.example). `STATION_CALL` and `STATION_NAME` are required; optional `APP_TITLE` defaults to `{STATION_CALL} Announcements`.

## Web UI

```bash
python -m app serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). First visit creates the Admin account; afterward **Admin** has full access and **Read-Only** can view and use Speak preview.

Operator guides:

- [How to schedule](docs/how-to-schedule.md) — baselines, overlays, spoken length / repeater timeout
- [Amateur Radio Newsline](docs/arnewsline.md) — download window, playlist, resume after busy
- [Radio backend](docs/radio-backend.md) — `stub` vs `dra` Protocols

Insert timed silence with `[pause:SECONDS]` (optional trailing `s`, max 10s). Invalid tags are left as ordinary text.

## CLI

```bash
python -m app <command> …
```

Commands include `speak`, `serve`, `voices`, `download` (Piper voices), and `users`. Full flags and examples stay in `python -m app <command> --help`.

## Configuration and API

- Env / defer timing / Piper voices: [docs/env-reference.md](docs/env-reference.md)
- HTTP routes overview: [docs/api-overview.md](docs/api-overview.md)

```bash
uv run basedpyright   # static types for app/
uv run pytest -q      # suite sets TTS_SKIP_DOTENV=1 so a local .env cannot affect results
```

CI runs pytest + basedpyright on `main`. Optional e2e: `uv run pytest -m e2e` (install Chromium once). Linux-only paths before push: `./scripts/ci-local.sh`.

## Data files

`data/announcements.json` (version 2) and `data/users.json` (version 1) are read on every request. An unrecognized version refuses to start instead of treating the file as empty (which would risk overwriting data on the next save). Empty / `{}` means a fresh install.

## Review history

Historical review-fix notes: [docs/code-review-findings.md](docs/code-review-findings.md). Bugs and enhancements are tracked on GitHub.
