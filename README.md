# DVRA Repeater Announcements

Station software for scheduled **Piper TTS** (and Amateur Radio Newsline) announcements on a repeater. A FastAPI web UI manages the library and schedules; on the NUC a DRA-36M backend keys PTT, watches COS, and plays USB audio. Audio is generated and played **in memory** — nothing is written as WAV/MP3 for on-air use.

## Requirements

- macOS or **64-bit Linux** (production: [docs/final-hardware/README.md](docs/final-hardware/README.md); DRA bench: [dra36m-check-tests.md](docs/final-hardware/dra36m-check-tests.md), [scripts/README.md](scripts/README.md))
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- PortAudio for CLI/local playback (`brew install portaudio` / `sudo apt install libportaudio2`)

## Setup

```bash
uv sync
source .venv/bin/activate   # optional; or use uv run …
```

Dependencies live in `pyproject.toml` + `uv.lock` (commit both). Use `uv add` / `uv remove` — do not `pip install` into the venv.

**Deploy to the NUC:** [docs/deploy-nuc.md](docs/deploy-nuc.md) (`scripts/deploy`, `serve.sh`, version bumps, restart behavior).

**Environment variables:** [docs/env-reference.md](docs/env-reference.md) and [`.env.example`](.env.example). Station branding defaults (`TTS_STATION_CALL`, `TTS_STATION_NAME`, `TTS_APP_TITLE`) can white-label the UI title.

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
