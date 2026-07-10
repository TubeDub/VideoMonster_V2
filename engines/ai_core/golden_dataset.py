"""Golden dataset helper — regression comparison for planner manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Required top-level keys in every planner manifest (v3.0 contract).
GOLDEN_MANIFEST_KEYS = frozenset(
    {
        "project_uuid",
        "ai_core_version",
        "planner_version",
        "pipeline_version",
        "task_id",
        "video_path",
        "target_lang",
        "source_lang",
        "created_at",
        "video_exists",
        "audio_track_count",
        "duration_ms",
        "segment_count_estimate",
        "language_hint",
        "content_type",
        "music_detected",
        "noise_level",
        "audio_quality_score",
        "capability_matrix",
        "smoke_tests",
        "confidence_scores",
        "complexity_score",
        "processing_strategy",
        "resource_estimation",
        "decision_reasons",
        "agent_dependencies",
        "success_criteria",
        "fallback_map",
        "agent_capabilities",
    }
)


def compare_manifest_keys(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return missing/extra keys vs golden contract."""
    present = set(manifest.keys())
    missing = sorted(GOLDEN_MANIFEST_KEYS - present)
    extra = sorted(present - GOLDEN_MANIFEST_KEYS)
    return {
        "ok": not missing,
        "missing": missing,
        "extra": extra,
        "key_count": len(present),
    }


def load_golden_reference(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
