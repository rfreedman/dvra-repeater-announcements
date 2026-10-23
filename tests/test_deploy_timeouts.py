"""DEPLOY_STOP_TIMEOUT and DEPLOY_START_TIMEOUT are checked before any NUC work."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy"


def _run_deploy(**env: str) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env.pop("DEPLOY_STOP_TIMEOUT", None)
    run_env.pop("DEPLOY_START_TIMEOUT", None)
    run_env.update(env)
    return subprocess.run(
        ["bash", str(DEPLOY)],
        cwd=ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_deploy_rejects_non_positive_timeouts_before_git():
    stop = _run_deploy(DEPLOY_STOP_TIMEOUT="0")
    assert stop.returncode == 1
    assert "Invalid DEPLOY_STOP_TIMEOUT=0" in stop.stderr
    assert "uncommitted" not in stop.stderr
    assert "Deploying" not in stop.stdout

    start = _run_deploy(DEPLOY_START_TIMEOUT="nope")
    assert start.returncode == 1
    assert "Invalid DEPLOY_START_TIMEOUT=nope" in start.stderr
    assert "uncommitted" not in start.stderr


def test_deploy_passes_timeouts_into_the_remote_script():
    text = DEPLOY.read_text(encoding="utf-8")
    assert '"$stop_timeout" "$start_timeout"' in text
    assert 'stop_timeout="$3"' in text
    assert 'start_timeout="$4"' in text
    assert 'stop_timeout="${DEPLOY_STOP_TIMEOUT:-30}"' in text
    assert 'start_timeout="${DEPLOY_START_TIMEOUT:-180}"' in text
