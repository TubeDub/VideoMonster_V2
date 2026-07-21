"""Pipeline Integrity / Stability v2 — feature gates.

PSA1 canonical flags (default OFF):
  VM_FLAG_IDENTITY_GUARD
  VM_FLAG_SEGMENT_NORMALIZER
  VM_FLAG_SLOT_BUDGET
  VM_FLAG_REVISION_MANAGER

Additional gates keep independent env keys; default OFF unless explicitly on.
"""

from __future__ import annotations

import os

from engines.pipeline_integrity.psa_flags import (
    PSA1_FLAG_ENV,
    identity_guard_flag,
    list_psa1_flags,
    psa_flag_enabled,
    revision_manager_flag,
    segment_normalizer_flag,
    slot_budget_flag,
)

_FLAG_IDS = (
    "identity_guard",
    "segment_normalizer",
    "slot_budget",
    "revision_manager",
    "smart_segmentation",
    "overflow_inspector",
)


def _env_bool(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return None
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    return None


def is_v2_enabled(flag_id: str, *, default: bool = False) -> bool:
    """PSA1+: default OFF. Env / config must explicitly enable."""
    if flag_id in PSA1_FLAG_ENV:
        return psa_flag_enabled(flag_id)

    # Non-PSA1 gates
    env_map = {
        "smart_segmentation": "VM_SMART_SEGMENTATION",
        "overflow_inspector": "VM_OVERFLOW_INSPECTOR",
    }
    key = env_map.get(flag_id, "")
    parsed = _env_bool(key) if key else None
    if parsed is not None:
        return parsed
    try:
        from engines.core.feature_flags import is_enabled

        return bool(is_enabled(flag_id))
    except Exception:
        return default


def identity_guard_enabled() -> bool:
    return identity_guard_flag()


def segment_normalizer_enabled() -> bool:
    return segment_normalizer_flag()


def slot_budget_enabled() -> bool:
    return slot_budget_flag()


def revision_manager_enabled() -> bool:
    return revision_manager_flag()


def smart_segmentation_enabled() -> bool:
    return is_v2_enabled("smart_segmentation", default=False)


def overflow_inspector_enabled() -> bool:
    return is_v2_enabled("overflow_inspector", default=False)


def list_v2_gates() -> dict[str, bool]:
    out = list_psa1_flags()
    out["smart_segmentation"] = smart_segmentation_enabled()
    out["overflow_inspector"] = overflow_inspector_enabled()
    return out
