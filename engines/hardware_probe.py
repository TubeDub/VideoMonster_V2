"""Hardware / acceleration probe for TubeDub diagnostics."""

from __future__ import annotations

import platform
import shutil
import subprocess
import time
from typing import Any

_HW_CACHE: dict[str, Any] | None = None
_HW_CACHE_AT = 0.0
_HW_CACHE_TTL = 300.0


def probe_whisper_device() -> tuple[str, str]:
    """
    Pick faster-whisper device/compute_type.
    Uses CUDA when ctranslate2 reports a GPU; otherwise CPU int8.
    Override with VM_WHISPER_DEVICE=cpu|cuda.
    """
    import os

    forced = (os.getenv("VM_WHISPER_DEVICE") or "").strip().lower()
    if forced == "cpu":
        return "cpu", "int8"
    if forced == "cuda":
        return "cuda", "float16"

    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass

    return "cpu", "int8"


def probe_hardware(*, force: bool = False) -> dict[str, Any]:
    """Collect acceleration info for performance reports and diagnostics."""
    global _HW_CACHE, _HW_CACHE_AT
    now = time.monotonic()
    if not force and _HW_CACHE is not None and (now - _HW_CACHE_AT) < _HW_CACHE_TTL:
        return dict(_HW_CACHE)
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "whisper_device": "cpu",
        "whisper_compute": "int8",
        "cuda_available": False,
        "cuda_devices": 0,
        "ffmpeg_hwaccel": [],
        "torch_cuda": False,
    }

    device, compute = probe_whisper_device()
    info["whisper_device"] = device
    info["whisper_compute"] = compute

    try:
        import ctranslate2

        n = int(ctranslate2.get_cuda_device_count())
        info["cuda_devices"] = n
        info["cuda_available"] = n > 0
    except Exception:
        pass

    try:
        import torch

        info["torch_cuda"] = bool(torch.cuda.is_available())
        if info["torch_cuda"] and not info["cuda_available"]:
            info["cuda_available"] = True
            info["cuda_devices"] = max(info["cuda_devices"], torch.cuda.device_count())
    except Exception:
        pass

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-hwaccels"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = [
                ln.strip()
                for ln in (proc.stdout or "").splitlines()
                if ln.strip() and not ln.strip().lower().startswith("hardware")
            ]
            info["ffmpeg_hwaccel"] = lines[:12]
        except Exception:
            pass

    _HW_CACHE = dict(info)
    _HW_CACHE_AT = now
    return dict(info)


def format_hardware_summary(hw: dict[str, Any] | None) -> list[str]:
    if not hw:
        return []
    lines = [
        "--- Hardware ---",
        f"Whisper: {hw.get('whisper_device')} ({hw.get('whisper_compute')})",
        f"CUDA devices: {hw.get('cuda_devices', 0)}",
        f"PyTorch CUDA: {hw.get('torch_cuda')}",
    ]
    accel = hw.get("ffmpeg_hwaccel") or []
    if accel:
        lines.append(f"FFmpeg hwaccels: {', '.join(accel[:8])}")
    else:
        lines.append("FFmpeg hwaccels: (none detected)")
    return lines
