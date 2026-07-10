"""Smoke tests — lightweight runtime health checks for Planner Agent v3.0."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.smoke_tests")


def _result(name: str, passed: bool, *, detail: str = "", elapsed_ms: float = 0.0) -> dict:
    return {
        "name": name,
        "passed": passed,
        "detail": detail,
        "elapsed_ms": round(elapsed_ms, 1),
    }


def run_smoke_tests(capability_matrix: dict | None = None) -> dict[str, Any]:
    """Run all smoke checks; never raises."""
    cap = capability_matrix or {}
    tests: list[dict] = []
    t0 = time.perf_counter()

    # Disk
    try:
        app_dir = Path(__file__).resolve().parents[2]
        out = app_dir / "output"
        out.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(out)
        free_gb = usage.free / (1024 ** 3)
        tests.append(_result("disk", free_gb >= 0.5, detail=f"free_gb={free_gb:.2f}"))
    except Exception as exc:
        tests.append(_result("disk", False, detail=str(exc)))

    # RAM (psutil optional)
    try:
        import psutil

        ram_gb = psutil.virtual_memory().available / (1024 ** 3)
        tests.append(_result("ram", ram_gb >= 1.0, detail=f"available_gb={ram_gb:.2f}"))
    except Exception as exc:
        tests.append(_result("ram", True, detail=f"skipped:{exc}"))

    # GPU
    try:
        from engines.hardware_probe import probe_hardware

        hw = probe_hardware()
        gpu_ok = bool(hw.get("cuda_available") or hw.get("torch_cuda"))
        tests.append(
            _result(
                "gpu",
                True,
                detail="cuda" if gpu_ok else "cpu_only",
            )
        )
    except Exception as exc:
        tests.append(_result("gpu", False, detail=str(exc)))

    # LLM ping
    try:
        from engines.ai_core import llm_gateway

        llm_ok = llm_gateway.is_available()
        tests.append(_result("llm_ping", llm_ok, detail="available" if llm_ok else "offline"))
    except Exception as exc:
        tests.append(_result("llm_ping", False, detail=str(exc)))

    # Whisper warmup (import + device probe only — no model load)
    try:
        from engines.hardware_probe import probe_whisper_device

        device, compute = probe_whisper_device()
        whisper_ok = bool(cap.get("asr", True))
        tests.append(
            _result(
                "whisper_warmup",
                whisper_ok,
                detail=f"{device}/{compute}",
            )
        )
    except Exception as exc:
        tests.append(_result("whisper_warmup", False, detail=str(exc)))

    # TTS probe
    try:
        tts_ok = bool(cap.get("tts"))
        if not tts_ok:
            import edge_tts  # noqa: F401

            tts_ok = True
        tests.append(_result("tts_test", tts_ok, detail="edge_tts" if tts_ok else "missing"))
    except Exception as exc:
        tests.append(_result("tts_test", False, detail=str(exc)))

    # FFmpeg
    ffmpeg_ok = bool(cap.get("ffmpeg") or shutil.which("ffmpeg"))
    tests.append(_result("ffmpeg", ffmpeg_ok, detail="found" if ffmpeg_ok else "missing"))

    elapsed = (time.perf_counter() - t0) * 1000
    passed = sum(1 for t in tests if t["passed"])
    return {
        "tests": tests,
        "passed": passed,
        "total": len(tests),
        "all_passed": passed == len(tests),
        "elapsed_ms": round(elapsed, 1),
    }
