"""P304 Hard Constraints + P317 Safety Validator."""

from __future__ import annotations

from typing import Any

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.pipeline_integrity.pipeline_state import PipelineState, get_pipeline_state
from engines.semantic_v3.types import SemanticSentence


def hard_constraint_check(
    sent: SemanticSentence,
    steps: list[str],
    *,
    profile: dict[str, Any],
) -> list[str]:
    """Return reject reasons (empty = ok). Never mutates sentence."""
    reasons: list[str] = []
    # Semantic Lock — rewrite forbidden when locked unless profile allows AND pre-lock
    if "semantic_rewrite" in steps:
        if sent.semantic_locked or getattr(sent, "lock_status", "") == "locked":
            reasons.append("semantic_lock")
        if not profile.get("allow_rewrite", False):
            reasons.append("policy_disallow_rewrite")
    # Entity / numbers / dates / terminology locks
    if sent.semantic_locked:
        if "semantic_rewrite" in steps:
            reasons.append("entity_lock")
            reasons.append("numbers_lock")
            reasons.append("dates_lock")
            reasons.append("terminology_lock")
    # Dialogue lock — don't merge across different dialogue speakers via rewrite
    if "sentence_merge" in steps and sent.is_dialogue and sent.dialogue_id:
        # merge itself is allowed; rewrite in dialogue is not
        if "semantic_rewrite" in steps:
            reasons.append("dialogue_lock")
    # Scene lock — rewrite across scenes blocked
    if "semantic_rewrite" in steps and getattr(sent, "scene_uuid", ""):
        if sent.semantic_locked:
            reasons.append("scene_lock")
    return list(dict.fromkeys(reasons))


def safety_validate(
    container: dict[str, Any] | None,
    *,
    require_locked: bool = False,
) -> None:
    """
    P317 — stop pipeline on architecture/safety failure.
    Read-only checks; raises ArchitectureViolation.
    """
    info = container or {}
    # Version contracts if stamped
    try:
        from engines.pipeline_integrity.contract_versions import require_contract_versions

        if any(k.endswith("_contract_version") for k in info):
            require_contract_versions(info)
    except Exception as exc:
        raise ArchitectureViolation(
            f"P317 Safety: contract failure: {exc}",
            stage="decision_policy",
            rule="safety_contracts",
        ) from exc

    state = get_pipeline_state(info) if info.get("pipeline_state") else None
    if state is not None and state == PipelineState.NEW and require_locked:
        raise ArchitectureViolation(
            "P317 Safety: pipeline still NEW",
            stage="decision_policy",
            rule="safety_state",
        )

    # Owner rules: Decision Policy must not claim text/audio ownership
    # (enforced by architecture tests — runtime stamp)
    info.setdefault("decision_policy_safety", True)
