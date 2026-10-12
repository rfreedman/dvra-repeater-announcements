#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Refresh the installed project so a version bump in pyproject.toml shows in the UI.
# --no-dev keeps test packages off production; --inexact leaves them alone on a dev machine.
if command -v uv >/dev/null 2>&1; then
  uv sync --inexact --no-dev
fi
source ./.venv/bin/activate
exec python -m app serve
