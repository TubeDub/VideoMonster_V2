"""Dub Studio configuration."""

from __future__ import annotations

import os
from pathlib import Path


def _flag(name: str, default: str = "0") -> bool:
    v = (os.getenv(name) or default).strip().lower()
    return v in ("1", "true", "yes", "on")


FEATURE_ID = "dub_studio"


def dub_studio_enabled() -> bool:
    if _flag("VM_DUB_STUDIO_ENABLED") or _flag("FEATURE_DUB_STUDIO", "0"):
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


def require_dub_studio() -> None:
    if not dub_studio_enabled():
        raise PermissionError(
            "Dub Studio disabled. Enable FEATURE_DUB_STUDIO in Developer Panel "
            "or set VM_DUB_STUDIO_ENABLED=1."
        )


def studio_config() -> dict:
    return {
        "max_tracks": int(os.getenv("VM_DUB_STUDIO_MAX_TRACKS", "32") or 32),
        "preview_latency_ms": int(os.getenv("VM_DUB_STUDIO_LATENCY_MS", "20") or 20),
        "time_stretch_max": float(os.getenv("VM_DUB_STUDIO_STRETCH_MAX", "1.35") or 1.35),
        "worker_threads": int(os.getenv("VM_DUB_STUDIO_WORKERS", "2") or 2),
    }
