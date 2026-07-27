# -*- coding: utf-8 -*-
"""Happy Path gate for TubeDub / VideoMonster_V2.

Stage 1 of the stabilization TZ: one predictable Simple-mode path.
Advanced shorteners (ADA, SSO, Meaning Fit, Timing-Aware LLM rewrite, …)
stay in the codebase but are OFF unless explicitly enabled.

Env overrides (Pro/dev only — Simple always stays on Happy Path):
  USE_ADVANCED_ADAPTATION=1
  VM_USE_ADVANCED_ADAPTATION=1
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("tubedub.happy_path")

# Default OFF — Simple mode is always Happy Path; Pro/dev may opt in via env.
USE_ADVANCED_ADAPTATION = False

# Happy Path timing (TZ Stage 3)
HAPPY_PATH_MAX_ATEMPO = 1.20
HAPPY_PATH_NO_SPEECH_TRIM = True

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", "disabled"})


def _env_bool(name: str) -> bool | None:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return None
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def resolve_user_mode(
    task_info: dict[str, Any] | None = None,
    *,
    explicit: str | None = None,
) -> str:
    """Return basic | pro | developer."""
    try:
        from engines.feature_flags.modes import normalize_mode
    except Exception:

        def normalize_mode(raw: str | None) -> str:  # type: ignore[misc]
            v = (raw or "basic").strip().lower()
            if v in ("dev", "developer"):
                return "developer"
            if v in ("pro", "professional", "advanced"):
                return "pro"
            return "basic"

    if explicit:
        return normalize_mode(explicit)
    info = task_info or {}
    for key in ("user_mode", "vm_user_mode", "ui_mode", "product_mode"):
        if info.get(key):
            return normalize_mode(str(info.get(key)))
    return "basic"


def is_simple_mode(
    task_info: dict[str, Any] | None = None,
    *,
    user_mode: str | None = None,
) -> bool:
    mode = resolve_user_mode(task_info, explicit=user_mode)
    return mode in ("basic", "simple", "")


def advanced_adaptation_enabled(
    task_info: dict[str, Any] | None = None,
    *,
    user_mode: str | None = None,
) -> bool:
    """True only when advanced shorteners may run.

    Simple/basic UI mode ALWAYS returns False (Happy Path), even if env=1.
    Pro/developer may enable via env or task_info override.
    """
    info = task_info or {}
    mode = resolve_user_mode(info, explicit=user_mode)

    # Hard rule from TZ: Simple mode = Happy Path only.
    if mode in ("basic", "simple"):
        return False

    # Explicit task override (Pro/dev tooling).
    if "use_advanced_adaptation" in info:
        try:
            return bool(info.get("use_advanced_adaptation"))
        except Exception:
            pass
    if "USE_ADVANCED_ADAPTATION" in info:
        try:
            return bool(info.get("USE_ADVANCED_ADAPTATION"))
        except Exception:
            pass

    for env_key in ("USE_ADVANCED_ADAPTATION", "VM_USE_ADVANCED_ADAPTATION"):
        parsed = _env_bool(env_key)
        if parsed is not None:
            return parsed

    # Feature flag registry (optional).
    try:
        from engines.core.feature_flags import is_enabled

        if is_enabled("advanced_adaptation", user_mode=mode):
            return True
    except Exception:
        pass

    return bool(USE_ADVANCED_ADAPTATION)


def stamp_happy_path_meta(
    task_info: dict[str, Any],
    *,
    user_mode: str | None = None,
) -> dict[str, Any]:
    """Write path labels into task info for clean logs / Review diagnostics."""
    mode = resolve_user_mode(task_info, explicit=user_mode)
    advanced = advanced_adaptation_enabled(task_info, user_mode=mode)
    meta = {
        "user_mode": mode,
        "happy_path": not advanced,
        "USE_ADVANCED_ADAPTATION": advanced,
        "adaptation_path": "advanced" if advanced else "happy_path",
        "adaptation_shorteners": (
            ["timing_aware", "meaning_fit", "sso", "ada", "soft_compress", "closed_loop_rewrite"]
            if advanced
            else ["naturalizer", "soft_compress"]
        ),
    }
    task_info.update(meta)
    logger.info(
        "happy_path: mode=%s advanced=%s path=%s",
        mode,
        advanced,
        meta["adaptation_path"],
    )
    return meta


def task_info_for(task_id: str | None = None) -> dict[str, Any]:
    """Best-effort lookup of running auto-dub task info."""
    if not task_id:
        return {}
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(str(task_id)) or {}
            info = task.get("info") if isinstance(task, dict) else None
            return dict(info) if isinstance(info, dict) else {}
    except Exception:
        return {}


def skip_advanced_text_shorteners(
    task_info: dict[str, Any] | None = None,
    *,
    user_mode: str | None = None,
    task_id: str | None = None,
) -> bool:
    """Convenience: True when ADA/SSO/MF/TAT/rewrite must be skipped."""
    info = task_info if task_info is not None else task_info_for(task_id)
    return not advanced_adaptation_enabled(info, user_mode=user_mode)


def happy_path_batch_translate(
    task_info: dict[str, Any] | None = None,
    *,
    task_id: str | None = None,
) -> bool:
    """True when translation should use larger MT batches (Stage 2)."""
    return skip_advanced_text_shorteners(task_info, task_id=task_id)
