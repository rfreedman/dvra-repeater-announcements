# Scripts

## `dra36m_check.py`

Bench verification for **DRA-36M** (CM119A USB audio + HID PTT/COS) before wiring the announcements app.

**Full procedures, pass criteria, and flags:** [`docs/final-hardware/dra36m-check-tests.md`](../docs/final-hardware/dra36m-check-tests.md)

**Record your rig:** [`docs/final-hardware/bench-record.md`](../docs/final-hardware/bench-record.md)

Quick start (JU1 on, JU5=A, **JU3=L** on both DRA; `-c left` is the script default):

```bash
source .venv/bin/activate
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib   # if hidapi from Homebrew
python scripts/dra36m_check.py retest -c left --with-voice --lead 0.6
```

Subcommands: `probe`, `monitor`, `snap`, `ptt`, `tone`, `smoke`, `voice`, `voice-gate`, `retest`.
