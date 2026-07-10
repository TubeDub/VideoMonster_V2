"""Cloud Platform configuration and feature gating."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

FEATURE_ID = "cloud_platform"


def _flag(name: str, default: str = "0") -> bool:
    v = (os.getenv(name) or default).strip().lower()
    return v in ("1", "true", "yes", "on")


def cloud_platform_enabled() -> bool:
    if _flag("VM_CLOUD_PLATFORM_ENABLED") or _flag("FEATURE_CLOUD_PLATFORM", "0"):
        return True
    try:
        from engines.feature_flags.manager import get_feature_manager

        app_dir = Path(__file__).resolve().parents[2]
        fm = get_feature_manager(app_dir)
        rec = fm.get(FEATURE_ID)
        if rec and fm._effective_enabled(rec) and not rec.auto_disabled:
            return True
    except Exception:
        pass
    return False


def require_cloud() -> None:
    if not cloud_platform_enabled():
        raise PermissionError(
            "Cloud Platform disabled. Enable FEATURE_CLOUD_PLATFORM in Developer Panel "
            "or set VM_CLOUD_PLATFORM_ENABLED=1."
        )


def cloud_config() -> dict[str, Any]:
    return {
        "chunk_size_mb": int(os.getenv("VM_CLOUD_CHUNK_MB", "8") or 8),
        "max_workers": int(os.getenv("VM_CLOUD_WORKERS", "3") or 3),
        "default_provider": os.getenv("VM_CLOUD_DEFAULT_PROVIDER", "local"),
        "tubedub_cloud_url": os.getenv("VM_TUBEDUB_CLOUD_URL", ""),
        "remote_jobs_enabled": _flag("VM_CLOUD_REMOTE_JOBS"),
    }
