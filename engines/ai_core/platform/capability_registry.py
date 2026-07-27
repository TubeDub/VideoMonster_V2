"""AI Capability Registry — single source of platform capabilities (Master Spec §4)."""

from __future__ import annotations

import logging
import shutil
from enum import Enum
from typing import Any

logger = logging.getLogger("tubedub.ai_core.capability_registry")


class CapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    READY = "READY"
    OFFLINE_ONLY = "OFFLINE_ONLY"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_INSTALLED = "NOT_INSTALLED"
    CPU_FALLBACK = "CPU_FALLBACK"
    CPU = "CPU"


def _safe_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def probe_llm() -> dict[str, Any]:
    status = CapabilityStatus.NOT_CONFIGURED
    model = ""
    provider = ""
    try:
        from engines.llm_adaptation_mode import detect_capabilities

        caps = detect_capabilities()
        if caps.get("llm_available"):
            if caps.get("cloud_api_available"):
                status = CapabilityStatus.AVAILABLE
            elif caps.get("local_llm_available"):
                status = CapabilityStatus.OFFLINE_ONLY
            else:
                status = CapabilityStatus.AVAILABLE
            model = str(caps.get("model") or "")
            provider = str(caps.get("provider") or "")
        else:
            status = CapabilityStatus.NOT_CONFIGURED
    except Exception as exc:
        logger.debug("LLM probe failed: %s", exc)
        status = CapabilityStatus.NOT_AVAILABLE
    return {
        "id": "llm",
        "label": "LLM",
        "status": status.value,
        "model": model,
        "provider": provider,
    }


def probe_asr() -> dict[str, Any]:
    status = CapabilityStatus.NOT_AVAILABLE
    device = "cpu"
    try:
        from engines.hardware_probe import probe_hardware, probe_whisper_device

        hw = probe_hardware()
        device, compute = probe_whisper_device()
        if _safe_import("faster_whisper") or shutil.which("ffmpeg"):
            status = CapabilityStatus.READY if hw.get("cuda_available") else CapabilityStatus.CPU_FALLBACK
        else:
            status = CapabilityStatus.NOT_INSTALLED
    except Exception:
        status = CapabilityStatus.CPU_FALLBACK if _safe_import("faster_whisper") else CapabilityStatus.NOT_AVAILABLE
    return {
        "id": "asr",
        "label": "ASR / Whisper",
        "status": status.value,
        "device": device,
    }


def probe_tts() -> dict[str, Any]:
    status = CapabilityStatus.NOT_AVAILABLE
    engine = "edge-tts"
    try:
        from engines.tts_text_path import tts_engine_available  # type: ignore[attr-defined]

        ok = bool(tts_engine_available())
        status = CapabilityStatus.READY if ok else CapabilityStatus.NOT_CONFIGURED
    except (ImportError, AttributeError):
        status = CapabilityStatus.READY if _safe_import("edge_tts") else CapabilityStatus.NOT_CONFIGURED
    return {"id": "tts", "label": "TTS", "status": status.value, "engine": engine}


def probe_voice_clone() -> dict[str, Any]:
    try:
        from engines.voice_platform.cloning import get_clone_adapter

        adapter = get_clone_adapter()
        available = bool(adapter.is_available())
        status = (
            CapabilityStatus.READY if available else CapabilityStatus.NOT_INSTALLED
        )
        return {
            "id": "voice_clone",
            "label": "Voice Clone",
            "status": status.value,
            "adapter_id": getattr(adapter, "adapter_id", "clone-null"),
            "hint": (
                None
                if available
                else "Install Coqui TTS / XTTS (pip install TTS) for minimal clone flow"
            ),
        }
    except Exception as exc:
        return {
            "id": "voice_clone",
            "label": "Voice Clone",
            "status": CapabilityStatus.NOT_INSTALLED.value,
            "error": str(exc)[:200],
        }


def probe_lipsync() -> dict[str, Any]:
    return {
        "id": "lipsync",
        "label": "LipSync",
        "status": CapabilityStatus.NOT_AVAILABLE.value,
    }


def probe_separation() -> dict[str, Any]:
    status = CapabilityStatus.NOT_INSTALLED
    try:
        if _safe_import("demucs") or _safe_import("torch"):
            status = CapabilityStatus.READY
    except Exception:
        pass
    return {"id": "separation", "label": "Separation", "status": status.value}


def probe_gpu() -> dict[str, Any]:
    status = CapabilityStatus.NOT_AVAILABLE
    try:
        from engines.hardware_probe import probe_hardware

        hw = probe_hardware()
        if hw.get("cuda_available"):
            status = CapabilityStatus.AVAILABLE
        else:
            status = CapabilityStatus.CPU
    except Exception:
        status = CapabilityStatus.CPU
    return {"id": "gpu", "label": "GPU", "status": status.value}


def build_registry() -> dict[str, Any]:
    """Full capability registry for Planner and agents (read-only)."""
    capabilities = [
        probe_llm(),
        probe_asr(),
        probe_tts(),
        probe_voice_clone(),
        probe_lipsync(),
        probe_separation(),
        probe_gpu(),
    ]
    by_id = {c["id"]: c for c in capabilities}
    return {
        "schema": "tubedub.capability_registry.v1",
        "capabilities": capabilities,
        "by_id": by_id,
        "llm_ready": by_id.get("llm", {}).get("status") in (
            CapabilityStatus.AVAILABLE.value,
            CapabilityStatus.OFFLINE_ONLY.value,
            CapabilityStatus.READY.value,
        ),
        "asr_ready": by_id.get("asr", {}).get("status") in (
            CapabilityStatus.READY.value,
            CapabilityStatus.CPU_FALLBACK.value,
        ),
        "tts_ready": by_id.get("tts", {}).get("status") == CapabilityStatus.READY.value,
    }


def get_capability(capability_id: str) -> dict[str, Any] | None:
    return build_registry()["by_id"].get(str(capability_id or ""))
