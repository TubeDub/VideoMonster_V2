"""Meaning Fit feature flags.

Env keys:
  VM_FLAG_MEANING_FIT
  VM_FLAG_MEANING_FIT_SHORTEN
  VM_FLAG_MEANING_FIT_EXPAND
  VM_FLAG_MEANING_FIT_BEFORE_LOCK

Hotfix: auto-dub path enables all four when env unset (see
``ensure_meaning_fit_enabled_for_dubbing``). Explicit ``=0`` still forces OFF.
"""

from __future__ import annotations

import os
from typing import Final

VM_FLAG_MEANING_FIT: Final = "VM_FLAG_MEANING_FIT"
VM_FLAG_MEANING_FIT_SHORTEN: Final = "VM_FLAG_MEANING_FIT_SHORTEN"
VM_FLAG_MEANING_FIT_EXPAND: Final = "VM_FLAG_MEANING_FIT_EXPAND"
VM_FLAG_MEANING_FIT_BEFORE_LOCK: Final = "VM_FLAG_MEANING_FIT_BEFORE_LOCK"

MF1_FLAG_ENV: Final[dict[str, str]] = {
    "meaning_fit": VM_FLAG_MEANING_FIT,
    "meaning_fit_shorten": VM_FLAG_MEANING_FIT_SHORTEN,
    "meaning_fit_expand": VM_FLAG_MEANING_FIT_EXPAND,
    "meaning_fit_before_lock": VM_FLAG_MEANING_FIT_BEFORE_LOCK,
}

# All MF flags for dubbing path
_DUBBING_MF_ENVS: Final[tuple[str, ...]] = (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_SHORTEN,
    VM_FLAG_MEANING_FIT_EXPAND,
    VM_FLAG_MEANING_FIT_BEFORE_LOCK,
)


def _parse_bool(raw: str) -> bool | None:
    v = (raw or "").strip().lower()
    if not v:
        return None
    if v in ("0", "false", "off", "no"):
        return False
    if v in ("1", "true", "on", "yes"):
        return True
    return None


def mf_flag_enabled(flag_id: str) -> bool:
    """Env wins; else feature_flags.json; else False."""
    if flag_id not in MF1_FLAG_ENV:
        return False
    parsed = _parse_bool(os.getenv(MF1_FLAG_ENV[flag_id]) or "")
    if parsed is not None:
        return parsed
    try:
        from engines.core.feature_flags import is_enabled

        return bool(is_enabled(flag_id))
    except Exception:
        return False


def ensure_meaning_fit_enabled_for_dubbing() -> dict[str, str]:
    """Turn MF flags ON for real auto-dub when env is unset.

    Does not override explicit ``VM_FLAG_*=0``. Returns applied env map.
    """
    applied: dict[str, str] = {}
    for key in _DUBBING_MF_ENVS:
        cur = os.getenv(key)
        if cur is None or str(cur).strip() == "":
            os.environ[key] = "1"
            applied[key] = "1"
        else:
            applied[key] = str(cur).strip()
    return applied


def meaning_fit_flag() -> bool:
    return mf_flag_enabled("meaning_fit")


def meaning_fit_shorten_flag() -> bool:
    return mf_flag_enabled("meaning_fit_shorten")


def meaning_fit_expand_flag() -> bool:
    return mf_flag_enabled("meaning_fit_expand")


def meaning_fit_before_lock_flag() -> bool:
    return mf_flag_enabled("meaning_fit_before_lock")


def list_mf1_flags() -> dict[str, bool]:
    return {fid: mf_flag_enabled(fid) for fid in MF1_FLAG_ENV}
