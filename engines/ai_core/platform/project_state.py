"""Project Manifest (read-only) and Project State (agent-scoped writes) — Master Spec §6–§7."""

from __future__ import annotations

import copy
from typing import Any

# Agent → state keys they may write (strict DAG, low coupling).
AGENT_WRITE_SCOPES: dict[str, frozenset[str]] = {
    "planner": frozenset({"manifest", "planner_decisions"}),
    "director": frozenset({"creative_brief"}),
    "translation": frozenset({"translated_text", "translator_used"}),
    "semantic": frozenset({"semantic_text", "semantic_scores"}),
    "timing": frozenset({"timing_text", "timing_slot_ms", "predicted_duration_ms"}),
    "grammar": frozenset({"grammar_text"}),
    "quality": frozenset({"quality_decision", "quality_passed", "quality_reasons", "quality_scores"}),
    "reviewer": frozenset({"final_text", "reviewer_approved", "voice_input"}),
    "voice_preparation": frozenset({"voice_prep_text", "emotion_tags_present"}),
    "voice": frozenset({"tts_file", "tts_duration_ms"}),
    "mix": frozenset({"mix_output", "mix_ok"}),
}


class ProjectStateGuard:
    """Validates segment field writes per agent responsibility (§7)."""

    @staticmethod
    def allowed_keys(agent_id: str) -> frozenset[str]:
        base = AGENT_WRITE_SCOPES.get(agent_id, frozenset())
        # Shared diagnostics keys any agent may append.
        return base | frozenset({"_agent_trace", "warnings"})

    @staticmethod
    def validate_segment_write(agent_id: str, before: dict, after: dict) -> list[str]:
        allowed = ProjectStateGuard.allowed_keys(agent_id)
        violations: list[str] = []
        if not allowed and agent_id not in AGENT_WRITE_SCOPES:
            return violations
        all_keys = set(before.keys()) | set(after.keys())
        for key in all_keys:
            if before.get(key) == after.get(key):
                continue
            if key not in allowed and not str(key).startswith(f"{agent_id}_"):
                violations.append(f"{agent_id} wrote forbidden key: {key}")
        return violations


def freeze_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return read-only copy of manifest for bus distribution (§6)."""
    return copy.deepcopy(manifest)


def merge_state_update(state: dict[str, Any], agent_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Apply agent patch to project state with shallow merge."""
    out = dict(state)
    out.setdefault("agent_patches", []).append({"agent": agent_id, "keys": list(patch.keys())})
    out.update(patch)
    return out
