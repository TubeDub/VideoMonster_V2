"""Feature flags bridge — VM_DEV_MODE, per-module flags, developer session."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_APP_DIR: Path | None = None


def IS_DEBUG_LEARNING_MODE() -> bool:
    """True when VM_DEBUG_MODE=1 env var is set OR data/debug_mode.json has enabled=true.

    In debug/learning mode the AutoDub pipeline NEVER stops on individual agent
    errors — all failures are recorded to OpenDDF and the pipeline continues.
    """
    if os.getenv("VM_DEBUG_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        flag_file = Path(__file__).resolve().parents[2] / "data" / "debug_mode.json"
        if not flag_file.is_file():
            return False
        import json

        with open(flag_file, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "enabled" in data:
            return bool(data.get("enabled"))
        return True
    except Exception:
        return False


def _app_dir() -> Path:
    global _APP_DIR
    if _APP_DIR is None:
        _APP_DIR = Path(__file__).resolve().parents[2]
    return _APP_DIR


def is_developer(
    *,
    request_headers: dict | None = None,
    request_cookies: dict | None = None,
) -> bool:
    """True when VM_DEV_MODE=1, VM_DEVELOPER_MODE=1, or license owner dev session."""
    if os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    from engines.module_registry.registry import is_developer_session

    return is_developer_session(
        request_headers=request_headers,
        request_cookies=request_cookies,
    )


def is_enabled(
    feature_id: str,
    *,
    user_mode: str = "basic",
    developer_session: bool | None = None,
    show_beta: bool = False,
) -> bool:
    """Check feature flag via FeatureManager (env override + release channel)."""
    from engines.feature_flags.manager import get_feature_manager

    dev = developer_session if developer_session is not None else is_developer()
    fm = get_feature_manager(_app_dir())
    return fm.is_enabled(
        feature_id,
        user_mode=user_mode,  # type: ignore[arg-type]
        developer_session=dev,
        show_beta=show_beta,
    )


def is_module_visible(
    module_id: str,
    *,
    user: dict[str, Any] | None = None,
    developer_session: bool | None = None,
    user_mode: str = "basic",
    show_beta: bool = False,
) -> bool:
    """Nav visibility: GREEN modules for users; dev sees YELLOW/RED when developer."""
    from engines.module_registry.registry import get_registry, module_visible_to_user

    dev = developer_session if developer_session is not None else is_developer()
    reg = get_registry(_app_dir())
    rec = reg.get(module_id)
    if not rec:
        return False
    if dev:
        return True
    if rec.feature_id and not is_enabled(
        rec.feature_id,
        user_mode=user_mode,
        developer_session=False,
        show_beta=show_beta,
    ):
        return False
    return module_visible_to_user(rec, show_beta=show_beta)
