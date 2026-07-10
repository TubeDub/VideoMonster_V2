"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0 — Environment telemetry.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any

from openddf.utils import filter_sensitive_data


def _cuda_info() -> dict[str, Any]:
    try:
        import torch  # type: ignore[import-untyped]  # optional, best-effort

        available = bool(torch.cuda.is_available())
        gpu_name = torch.cuda.get_device_name(0) if available and torch.cuda.device_count() > 0 else None
        return {
            "cuda_available": available,
            "gpu_name": gpu_name,
            "device_count": torch.cuda.device_count() if available else 0,
        }
    except Exception:
        return {"cuda_available": False, "gpu_name": None, "device_count": 0}


def collect_environment_info() -> dict[str, Any]:
    """Collect OS, Python, process, and optional CUDA metadata."""
    info: dict[str, Any] = {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
        "process_id": os.getpid(),
        "platform": platform.platform(),
        "cuda": _cuda_info(),
    }
    env_subset = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in ("PATH", "PYTHONPATH", "VIRTUAL_ENV", "HOME", "USERPROFILE")
    }
    info["environment_variables"] = filter_sensitive_data(env_subset)
    return filter_sensitive_data(info)
