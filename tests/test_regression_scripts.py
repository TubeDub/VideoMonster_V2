"""Run legacy scripts/test_*.py as subprocess regression (CI-safe subset)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Fast, offline-safe scripts — CI subset (~30s total)
FAST_REGRESSION_SCRIPTS = [
    "test_prepare_pairs.py",
    "test_module_registry.py",
    "test_feature_flags.py",
    "test_dub_studio.py",
    "test_prosody_lipsync.py",
    "test_uk_dinner_fix.py",
    "test_cloud_platform.py",
    "test_route_planner.py",
    "test_placeholder_guard.py",
]


@pytest.mark.parametrize("script", FAST_REGRESSION_SCRIPTS)
def test_regression_script(script: str) -> None:
    path = ROOT / "scripts" / script
    assert path.is_file(), f"missing {script}"
    env = os.environ.copy()
    env["VM_DEV_MODE"] = "1"
    env.setdefault("VM_PREPARE_WARMUP", "0")
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or "") + (proc.stderr or "")
        pytest.fail(f"{script} failed:\n{tail[-4000:]}")
