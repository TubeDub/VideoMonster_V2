"""PSA1 — skeleton invariants / guards (no-op when flags OFF).

Full enforcement is later phases (PSA2+). PSA1 only provides:
  - flag gating (default OFF)
  - typed invariant errors
  - stub entrypoints that pass through when disabled
"""

from __future__ import annotations

from typing import Any

from engines.pipeline_integrity.exceptions import (
    IdentityMismatchError,
    PipelineIntegrityError,
    PipelineValidationError,
)
from engines.pipeline_integrity.psa_flags import (
    identity_guard_flag,
    revision_manager_flag,
    segment_normalizer_flag,
    slot_budget_flag,
)


class SegmentNormalizerInvariantError(PipelineIntegrityError):
    """Segment Normalizer invariant violated (PSA skeleton)."""

    code = "segment_normalizer_invariant"


class SlotBudgetInvariantError(PipelineValidationError):
    """Slot Budget First invariant violated (PSA skeleton)."""

    code = "slot_budget_invariant"


class RevisionInvariantError(PipelineIntegrityError):
    """Revision / ownership UUID invariant violated (PSA skeleton)."""

    code = "revision_invariant"


# Re-export identity error for PSA callers
__all__ = [
    "IdentityMismatchError",
    "SegmentNormalizerInvariantError",
    "SlotBudgetInvariantError",
    "RevisionInvariantError",
    "skeleton_identity_guard",
    "skeleton_segment_normalizer",
    "skeleton_slot_budget",
    "skeleton_revision_manager",
]


def skeleton_identity_guard(
    segments_data: list[dict[str, Any]] | None = None,
    *,
    stage: str = "psa1",
) -> dict[str, Any]:
    """No-op when VM_FLAG_IDENTITY_GUARD is OFF."""
    if not identity_guard_flag():
        return {"enabled": False, "noop": True, "stage": stage, "module": "identity_guard"}
    # PSA1: flag ON → skeleton only (no hard enforcement yet)
    return {
        "enabled": True,
        "noop": True,
        "stage": stage,
        "module": "identity_guard",
        "skeleton": True,
        "checked": len(segments_data or []),
    }


def skeleton_segment_normalizer(
    segments: list[str] | None = None,
    timing_map: list[Any] | None = None,
) -> dict[str, Any]:
    """No-op when VM_FLAG_SEGMENT_NORMALIZER is OFF — returns inputs unchanged."""
    texts = list(segments or [])
    timing = list(timing_map or [])
    if not segment_normalizer_flag():
        return {
            "enabled": False,
            "noop": True,
            "module": "segment_normalizer",
            "segments": texts,
            "timing_map": timing,
        }
    return {
        "enabled": True,
        "noop": True,
        "skeleton": True,
        "module": "segment_normalizer",
        "segments": texts,
        "timing_map": timing,
    }


def skeleton_slot_budget(
    segments_data: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """No-op when VM_FLAG_SLOT_BUDGET is OFF."""
    if not slot_budget_flag():
        return {
            "enabled": False,
            "noop": True,
            "module": "slot_budget",
            "tts_allowed": True,
        }
    return {
        "enabled": True,
        "noop": True,
        "skeleton": True,
        "module": "slot_budget",
        "tts_allowed": True,
        "checked": len(segments_data or []),
    }


def skeleton_revision_manager(seg: dict[str, Any] | None = None) -> dict[str, Any]:
    """No-op when VM_FLAG_REVISION_MANAGER is OFF."""
    if not revision_manager_flag():
        return {"enabled": False, "noop": True, "module": "revision_manager"}
    return {
        "enabled": True,
        "noop": True,
        "skeleton": True,
        "module": "revision_manager",
        "segment_id": (seg or {}).get("segment_id"),
    }
