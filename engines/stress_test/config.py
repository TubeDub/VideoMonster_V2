"""Stress Test configuration."""

from __future__ import annotations

import os
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def app_dir(base: Path | None = None) -> Path:
    return base or Path(__file__).resolve().parent.parent.parent


def stress_tests_dir(base: Path | None = None) -> Path:
    return app_dir(base) / "data" / "stress_tests"


def reports_dir(base: Path | None = None) -> Path:
    d = app_dir(base) / "output" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def history_dir(base: Path | None = None) -> Path:
    d = app_dir(base) / "output" / "stress_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir(base: Path | None = None) -> Path:
    d = app_dir(base) / "output" / "stress_tests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def poll_interval_sec() -> float:
    try:
        return max(0.5, float(os.getenv("VM_STRESS_POLL_SEC", "1.0")))
    except ValueError:
        return 1.0


def task_timeout_sec() -> int:
    try:
        return max(60, int(os.getenv("VM_STRESS_TASK_TIMEOUT", "900")))
    except ValueError:
        return 900


def is_module_available() -> bool:
    return True
