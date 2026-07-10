"""Capability matrix — detect LLM/ASR/TTS/GPU and related runtime status."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.capability_matrix")


def _safe_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def build_capability_matrix() -> dict[str, Any]:
    """Probe local/cloud capabilities without mutating pipeline state."""
    matrix: dict[str, Any] = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "pydub": _safe_import("pydub"),
        "langdetect": _safe_import("langdetect"),
        "llm": False,
        "asr": False,
        "tts": False,
        "gpu": False,
        "cuda": False,
        "whisper_model": False,
        "details": {},
    }

    try:
        from engines.ai_core import llm_gateway

        matrix["llm"] = bool(llm_gateway.is_available())
        matrix["details"]["llm"] = "available" if matrix["llm"] else "unavailable"
    except Exception as exc:
        matrix["details"]["llm"] = str(exc)

    try:
        from engines.ffmpeg_paths import find_ffmpeg

        matrix["ffmpeg"] = bool(find_ffmpeg() or matrix["ffmpeg"])
    except Exception:
        pass

    try:
        from engines.hardware_probe import probe_hardware, probe_whisper_device

        hw = probe_hardware()
        device, compute = probe_whisper_device()
        matrix["gpu"] = bool(hw.get("cuda_available") or hw.get("torch_cuda"))
        matrix["cuda"] = bool(hw.get("cuda_available"))
        matrix["asr"] = True  # faster-whisper importable path
        matrix["details"]["whisper_device"] = device
        matrix["details"]["whisper_compute"] = compute
        matrix["details"]["hardware"] = {
            "platform": hw.get("platform"),
            "cuda_devices": hw.get("cuda_devices", 0),
        }
    except Exception as exc:
        matrix["details"]["asr"] = str(exc)
        matrix["asr"] = _safe_import("faster_whisper")

    try:
        from engines.tts_text_path import tts_engine_available  # type: ignore[attr-defined]

        matrix["tts"] = bool(tts_engine_available())
    except (ImportError, AttributeError):
        matrix["tts"] = _safe_import("edge_tts") or _safe_import("gtts")

    try:
        models_dir = Path(__file__).resolve().parents[2] / "models"
        whisper_dirs = list(models_dir.glob("**/faster-whisper-*"))
        matrix["whisper_model"] = len(whisper_dirs) > 0 or matrix["asr"]
    except Exception:
        matrix["whisper_model"] = matrix["asr"]

    try:
        from engines.ai_core.platform.capability_registry import build_registry

        registry = build_registry()
        matrix["registry"] = registry
        matrix["registry_schema"] = registry.get("schema")
        by_id = registry.get("by_id") or {}
        llm_entry = by_id.get("llm") or {}
        llm_status = str(llm_entry.get("status") or "")
        matrix["llm_status"] = llm_status
        if llm_status in ("AVAILABLE", "OFFLINE_ONLY", "READY"):
            matrix["llm"] = matrix["llm"] or True
        matrix["details"]["capability_registry"] = {
            c["id"]: c.get("status") for c in (registry.get("capabilities") or [])
        }
    except Exception as exc:
        matrix["details"]["capability_registry"] = str(exc)

    return matrix
