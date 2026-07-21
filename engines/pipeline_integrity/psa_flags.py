"""PSA1 — Pipeline Stability v2 feature flags (default OFF).

Env keys (CURSOR-READY TZ):
  VM_FLAG_IDENTITY_GUARD
  VM_FLAG_SEGMENT_NORMALIZER
  VM_FLAG_SLOT_BUDGET
  VM_FLAG_REVISION_MANAGER

When unset → OFF. Stubs must no-op while OFF.
"""

from __future__ import annotations

import os
from typing import Final

VM_FLAG_IDENTITY_GUARD: Final = "VM_FLAG_IDENTITY_GUARD"
VM_FLAG_SEGMENT_NORMALIZER: Final = "VM_FLAG_SEGMENT_NORMALIZER"
VM_FLAG_SLOT_BUDGET: Final = "VM_FLAG_SLOT_BUDGET"
VM_FLAG_REVISION_MANAGER: Final = "VM_FLAG_REVISION_MANAGER"

PSA1_FLAG_ENV: Final[dict[str, str]] = {
    "identity_guard": VM_FLAG_IDENTITY_GUARD,
    "segment_normalizer": VM_FLAG_SEGMENT_NORMALIZER,
    "slot_budget": VM_FLAG_SLOT_BUDGET,
    "revision_manager": VM_FLAG_REVISION_MANAGER,
}

_LEGACY_ENV: Final[dict[str, str]] = {
    "identity_guard": "VM_IDENTITY_GUARD",
    "segment_normalizer": "VM_SEGMENT_NORMALIZER",
    "slot_budget": "VM_SLOT_BUDGET",
    "revision_manager": "VM_REVISION_MANAGER",
}


def _parse_bool(raw: str) -> bool | None:
    v = (raw or "").strip().lower()
    if not v:
        return None
    if v in ("0", "false", "off", "no"):
        return False
    if v in ("1", "true", "on", "yes"):
        return True
    return None


def psa_flag_enabled(flag_id: str) -> bool:
    """Return True only when explicitly enabled. Default OFF."""
    if flag_id not in PSA1_FLAG_ENV:
        return False
    parsed = _parse_bool(os.getenv(PSA1_FLAG_ENV[flag_id]) or "")
    if parsed is not None:
        return parsed
    legacy = _LEGACY_ENV.get(flag_id)
    if legacy:
        parsed = _parse_bool(os.getenv(legacy) or "")
        if parsed is not None:
            return parsed
    try:
        from engines.core.feature_flags import is_enabled

        return bool(is_enabled(flag_id))
    except Exception:
        return False


def identity_guard_flag() -> bool:
    return psa_flag_enabled("identity_guard")


def segment_normalizer_flag() -> bool:
    return psa_flag_enabled("segment_normalizer")


def slot_budget_flag() -> bool:
    return psa_flag_enabled("slot_budget")


def revision_manager_flag() -> bool:
    return psa_flag_enabled("revision_manager")


def list_psa1_flags() -> dict[str, bool]:
    return {fid: psa_flag_enabled(fid) for fid in PSA1_FLAG_ENV}
