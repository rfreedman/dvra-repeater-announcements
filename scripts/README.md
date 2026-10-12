# Scripts

## `deploy.sh`

Run from the dev machine: stops the app on the NUC, runs `git pull` there, and restarts `./serve.sh` in the background. The host defaults to `repeater-nuc`; override it with `DEPLOY_HOST` or the first argument. Needs uv on the NUC. Details: [README — Deploying (NUC)](../README.md#deploying-nuc).

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
