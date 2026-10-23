# Scripts

## `start-remote`

Run from the **dev machine**: SSH to the NUC and start `./serve.sh` with `setsid`/`nohup` (survives logoff on the NUC). Does not stop a running app or pull git. Log on the NUC: `data/logs/serve.out`. Same host/path env as deploy (`DEPLOY_HOST`, `DEPLOY_PATH`).

```bash
scripts/start-remote                 # default host repeater-nuc
scripts/start-remote other-host      # or DEPLOY_HOST=other-host
```

## `stop-remote`

Run from the **dev machine**: SSH to the NUC and stop `python -m app serve` with SIGTERM (never SIGKILL). Waits up to `DEPLOY_STOP_TIMEOUT` seconds (default 30), same as deploy. Exits 0 if the app was not running.

```bash
scripts/stop-remote
scripts/stop-remote other-host
```

## `deploy`

Run from the dev machine: stops the app on the NUC, runs `git pull` there, and restarts `./serve.sh` in the background. The host defaults to `repeater-nuc`; override it with `DEPLOY_HOST` or the first argument. `DEPLOY_STOP_TIMEOUT` (default 30) and `DEPLOY_START_TIMEOUT` (default 180) are the stop and startup waits, in seconds. Needs uv on the NUC. Details: [README — Deploying (NUC)](../README.md#deploying-nuc).

```bash
scripts/deploy
scripts/deploy other-host
```

## `dra36m_check.py`

Bench verification for **DRA-36M** (CM119A USB audio + HID PTT/COS) before wiring the announcements app.

**Full procedures, pass criteria, and flags:** [`docs/final-hardware/dra36m-check-tests.md`](../docs/final-hardware/dra36m-check-tests.md)

**Record your rig:** [`docs/final-hardware/bench-record.md`](../docs/final-hardware/bench-record.md)

Quick start (JU1 on, JU5=A, **JU3=L** on both DRA; `-c left` is the script default):

```bash
source .venv/bin/activate
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib   # if hidapi from Homebrew (macOS only)
python scripts/dra36m_check.py retest -c left --with-voice --lead 0.6
```

**Linux (NUC):** udev rules + `plugdev` for CM119 HID/USB — [`dra36m-check-tests.md`](../docs/final-hardware/dra36m-check-tests.md#linux-nuc-udev-and-plugdev).

Subcommands: `probe`, `monitor`, `snap`, `ptt`, `tone`, `smoke`, `voice`, `voice-gate`, `retest`.
