#!/usr/bin/env bash
# Run the default pytest suite in Ubuntu (matches GitHub Actions Linux paths).
# Uses a venv inside the container (/tmp) — does not modify the host .venv.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
docker run --rm \
  -v "${repo_root}:/w" \
  -w /w \
  -e TTS_SKIP_DOTENV=1 \
  -e GITHUB_ACTIONS=true \
  -e UV_PROJECT_ENVIRONMENT=/tmp/dvra-ci-venv \
  ubuntu:24.04 bash -lc '
  set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends libportaudio2 curl ca-certificates tzdata
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.12
  uv sync --frozen --all-groups --python 3.12
  uv run pytest -q
'
