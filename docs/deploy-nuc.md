# Deploying (NUC)

The NUC runs the app the same way as dev: a git clone, uv, and `./serve.sh`. Install uv once on the NUC (it goes in `~/.local/bin`):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

From the dev machine, deploy whatever is pushed to `origin/main`:

```bash
scripts/deploy                    # default host repeater-nuc
scripts/deploy other-host         # or DEPLOY_HOST=other-host; DEPLOY_PATH overrides the NUC repo path
```

The script refuses to run with uncommitted or unpushed local changes. On the NUC it checks that uv is present, that the clone is clean, and that `origin/main` fast-forwards, all before touching the app. If `origin/main` has new commits, its `pyproject.toml` version must be greater than the one the NUC is running (bump with `uv version --bump patch`, commit, push); otherwise the deploy stops and leaves the app running. A redeploy with no new commits just restarts the app. Then it stops the running `python -m app serve` with SIGTERM, pulls, and starts `./serve.sh` in the background, which syncs dependencies. It waits for `Startup complete` in the log and warns if the radio mode is `stub`. Background output goes to `data/logs/serve.out` on the NUC. If the app does not stop within `DEPLOY_STOP_TIMEOUT` seconds (default 30), the deploy aborts without updating; it never SIGKILLs the app, because that can leave PTT keyed. Startup must log `Startup complete` within `DEPLOY_START_TIMEOUT` seconds (default 180) or the deploy reports failure. Both values are positive integer seconds.

If deploy reports **NUC working tree has local changes** and `git status` shows only `uv.lock`, an older `./serve.sh` may have rewritten the lock on Linux. On the NUC, discard that change, then redeploy after `origin/main` includes `./serve.sh` with `uv sync --frozen`:

```bash
ssh repeater-nuc 'cd /home/rfreedman/projects/dvra-repeater-announcements && git restore uv.lock'
```

After `./serve.sh` is frozen, restarts should not modify `uv.lock` again; confirm with `git status` on the NUC if you want to be sure.

#### Process restart and in-memory state

Deploy and any manual restart start a **new process**. Nothing below is written to disk:

- **Busy retries** — APScheduler `defer:` jobs and in-flight manual-trigger retries are dropped. A slot whose fire time has already passed is **not** played late after restart (see startup behavior below).
- **Login rate limit** — Failed sign-in counters (`429` after `TTS_LOGIN_MAX_FAILURES` failures, default five, per username from the same client within `TTS_LOGIN_WINDOW_SECONDS`, default 300 seconds) reset. Legitimate sessions in cookies may still work until they expire.

After an outage or restart, use **Trigger now** or wait for the next slot if something did not air; check logs for deferred fires that will not run until you trigger again.

To install the locked versions by hand, without test packages:

```bash
uv sync --frozen --no-dev
```

Without uv on the NUC, generate a pinned `requirements.txt` on the dev machine and install it with pip:

```bash
uv export --format requirements-txt --no-dev -o requirements.txt   # dev machine
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt  # NUC, from the repo root
```

The exported file pins every package (with hashes) and keeps platform markers, so the macOS-only `pyobjc-framework-Cocoa` is skipped on Linux. It ends with `-e .`, which installs this project in editable mode (that is where the app gets its version), so run the `pip install` from the repo root. It is gitignored: regenerate it after any dependency change rather than editing it.

`./serve.sh` runs `uv sync --frozen --inexact --no-dev` before starting (when uv is installed), so the venv matches the committed `uv.lock` and the lock file itself is left unchanged.

### Versioning

The version is written once, in `pyproject.toml`. The app reads it from the installed package (`importlib.metadata`), and it appears in the UI header and the API (`/docs`, `/openapi.json`). Do not add a version literal anywhere else.

```bash
uv version                 # show the current version
uv version --bump patch    # 0.2.0 -> 0.2.1 (also minor / major); updates pyproject.toml and uv.lock
```

The project is installed in editable mode, but the version is recorded at install time, so a bump shows up after the next `uv sync`. `./serve.sh` and any `uv run ...` command do that automatically. If you start the app another way, run `uv sync` first. `tests/test_api.py::test_installed_version_matches_pyproject` fails when the installed version is stale.

The first run downloads the default voice into `voices/` (gitignored).

```bash
python -m app download
python -m app download amy ryan
```

