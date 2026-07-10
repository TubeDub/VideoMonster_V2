"""Standard benchmark clip for reproducible performance measurements."""

from __future__ import annotations

import shutil
from pathlib import Path

BENCHMARK_NAME = "benchmark_video.mp4"


def benchmark_video_path(app_dir: Path | None = None) -> Path:
    root = app_dir or Path(__file__).resolve().parents[1]
    return root / "data" / "stress_tests" / BENCHMARK_NAME


def ensure_benchmark_video(app_dir: Path | None = None) -> Path | None:
    """Ensure data/stress_tests/benchmark_video.mp4 exists (copy E2E clip if needed)."""
    root = app_dir or Path(__file__).resolve().parents[1]
    target = benchmark_video_path(root)
    if target.is_file() and target.stat().st_size > 5000:
        return target

    stress_dir = target.parent
    stress_dir.mkdir(parents=True, exist_ok=True)

    candidates = [
        root / "uploads" / "test_e2e_speech.mp4",
        root / "data" / "stress_tests" / "test_e2e_speech.mp4",
    ]
    for src in candidates:
        if src.is_file() and src.stat().st_size > 5000:
            shutil.copy2(src, target)
            return target

    try:
        import importlib.util

        e2e_path = root / "scripts" / "e2e_test.py"
        if e2e_path.is_file():
            spec = importlib.util.spec_from_file_location("vm_e2e_test", e2e_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if mod.ensure_test_video():
                    src = root / "uploads" / "test_e2e_speech.mp4"
                    if src.is_file():
                        shutil.copy2(src, target)
                        return target
    except Exception:
        pass

    return None
