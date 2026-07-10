"""AI Core feature flags registry (Master Spec v3.0 §18)."""

from __future__ import annotations

import os
from typing import Any

# Platform features — env VM_AI_FEATURE_<NAME>=1 overrides FeatureManager.
# Aliases map TZ names → canonical ids (backward compatible).
_FEATURE_ALIASES: dict[str, str] = {
    "streaming": "streaming_pipeline",
    "qualitygate": "quality_gate",
    "reviewer": "reviewer",
    "voicedna": "voice_dna",
    "adaptivetiming": "adaptive_timing_predictor",
    "sqlitememory": "ai_memory",
    "cloudproviders": "cloud_providers",
    "parallelpipeline": "parallel_pipeline",
}

PLATFORM_FEATURES: dict[str, dict[str, Any]] = {
    "streaming_pipeline": {
        "label": "Streaming Pipeline",
        "default": True,
        "env_key": "VM_AI_FEATURE_STREAMING",
        "tz_id": "Streaming",
    },
    "quality_gate": {
        "label": "Quality Gate",
        "default": True,
        "env_key": "VM_AI_FEATURE_QUALITY_GATE",
        "tz_id": "QualityGate",
    },
    "ai_memory": {
        "label": "AI Memory",
        "default": True,
        "env_key": "VM_AI_FEATURE_AI_MEMORY",
        "tz_id": "SQLiteMemory",
    },
    "reviewer": {
        "label": "AI Reviewer",
        "default": True,
        "env_key": "VM_AI_FEATURE_REVIEWER",
        "tz_id": "Reviewer",
    },
    "adaptive_timing_predictor": {
        "label": "Adaptive Timing Predictor",
        "default": False,
        "env_key": "VM_AI_FEATURE_ADAPTIVE_TIMING",
        "tz_id": "AdaptiveTiming",
    },
    "voice_dna": {
        "label": "Voice DNA",
        "default": False,
        "env_key": "VM_AI_FEATURE_VOICE_DNA",
        "tz_id": "VoiceDNA",
    },
    "lipsync": {
        "label": "LipSync",
        "default": False,
        "env_key": "VM_AI_FEATURE_LIPSYNC",
    },
    "cloud_providers": {
        "label": "Cloud LLM Providers",
        "default": True,
        "env_key": "VM_AI_FEATURE_CLOUD_PROVIDERS",
        "tz_id": "CloudProviders",
    },
    "parallel_pipeline": {
        "label": "Parallel Pipeline",
        "default": False,
        "env_key": "VM_AI_FEATURE_PARALLEL",
        "tz_id": "ParallelPipeline",
    },
}


def _canonical_id(feature_id: str) -> str:
    fid = str(feature_id or "").strip().lower().replace("-", "_")
    return _FEATURE_ALIASES.get(fid, fid)


def is_platform_feature_enabled(feature_id: str, *, developer_session: bool = False) -> bool:
    fid = _canonical_id(feature_id)
    meta = PLATFORM_FEATURES.get(fid)
    if not meta:
        return False
    env_key = str(meta.get("env_key") or "")
    if env_key:
        raw = os.getenv(env_key, "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
    # Use FeatureManager only when feature is registered; otherwise platform default.
    try:
        from pathlib import Path

        from engines.feature_flags.manager import get_feature_manager

        app_dir = Path(__file__).resolve().parents[3]
        fm = get_feature_manager(app_dir)
        if fm.get(fid):
            from engines.core.feature_flags import is_enabled

            return bool(is_enabled(fid, developer_session=developer_session))
    except Exception:
        pass
    return bool(meta.get("default", False))


def list_platform_features(*, developer_session: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fid, meta in PLATFORM_FEATURES.items():
        out.append(
            {
                "id": fid,
                "tz_id": meta.get("tz_id", fid),
                "label": meta.get("label", fid),
                "enabled": is_platform_feature_enabled(fid, developer_session=developer_session),
                "default": meta.get("default", False),
            }
        )
    return out
