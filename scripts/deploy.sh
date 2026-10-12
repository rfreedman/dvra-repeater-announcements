#!/usr/bin/env bash
# Deploy origin/main to the NUC: stop the app, git pull, start serve.sh, wait for startup.
# Usage: scripts/deploy.sh [host]   (env: DEPLOY_HOST, DEPLOY_PATH, DEPLOY_BRANCH)
set -euo pipefail

HOST="${1:-${DEPLOY_HOST:-repeater-nuc}}"
REMOTE_PATH="${DEPLOY_PATH:-/home/rfreedman/projects/dvra-repeater-announcements}"
BRANCH="${DEPLOY_BRANCH:-main}"

cd "$(dirname "$0")/.."

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Local working tree has uncommitted changes; commit and push first." >&2
  exit 1
fi
git fetch --quiet origin "$BRANCH"
if [[ -n "$(git rev-list "origin/$BRANCH..$BRANCH")" ]]; then
  echo "Local $BRANCH has commits not pushed to origin/$BRANCH; push first." >&2
  exit 1
fi

echo "Deploying origin/$BRANCH to $HOST:$REMOTE_PATH"
ssh "$HOST" bash -s -- "$REMOTE_PATH" "$BRANCH" <<'REMOTE'
set -euo pipefail
cd "$1"
branch="$2"
# Non-interactive ssh skips ~/.bashrc, where the uv installer adds itself; serve.sh needs uv to sync.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
pattern='python -m app serve'
stop_timeout=30
start_timeout=180
out=data/logs/serve.out

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found on the NUC (serve.sh needs it to sync dependencies); app left running." >&2
  exit 1
fi
git fetch --quiet origin "$branch"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "NUC working tree has local changes; app left running." >&2
  git status --short --untracked-files=no >&2
  exit 1
fi
if ! git merge-base --is-ancestor HEAD "origin/$branch"; then
  echo "NUC HEAD cannot fast-forward to origin/$branch; app left running." >&2
  exit 1
fi

pids="$(pgrep -u "$USER" -f "$pattern" || true)"
if [[ -n "$pids" ]]; then
  echo "Stopping app (pid $pids)"
  kill -TERM $pids
  for ((i = 0; i < stop_timeout; i++)); do
    pgrep -u "$USER" -f "$pattern" >/dev/null || break
    sleep 1
  done
  if pgrep -u "$USER" -f "$pattern" >/dev/null; then
    # No SIGKILL: a hard kill mid-transmission can leave PTT keyed.
    echo "App still running after ${stop_timeout}s (pid $(pgrep -u "$USER" -f "$pattern" | tr '\n' ' '))." >&2
    echo "Not updating. Check it and stop it yourself, then re-run the deploy." >&2
    exit 1
  fi
else
  echo "App was not running"
fi

old="$(git rev-parse --short HEAD)"
git merge --quiet --ff-only "origin/$branch"
new="$(git rev-parse --short HEAD)"
version="$(sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml | head -n 1)"
echo "Updated $old -> $new (version $version)"
mkdir -p "$(dirname "$out")"
setsid nohup ./serve.sh >"$out" 2>&1 </dev/null &
pid=$!
echo "Starting app (output: $1/$out)"

for ((i = 0; i < start_timeout; i++)); do
  if line="$(grep -m 1 'Startup complete' "$out")"; then
    echo "$line"
    if [[ "$line" == *"radio mode: stub"* ]]; then
      echo "WARNING: radio mode is stub; set TTS_RADIO_BACKEND=dra in .env for on-air playback." >&2
    fi
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "App exited during startup. Last output:" >&2
    tail -n 40 "$out" >&2
    exit 1
  fi
  sleep 1
done
echo "No 'Startup complete' after ${start_timeout}s. Last output:" >&2
tail -n 40 "$out" >&2
exit 1
REMOTE
