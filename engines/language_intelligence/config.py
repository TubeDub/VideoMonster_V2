"""Feature flags — Language Intelligence v2."""

from __future__ import annotations

import os


def is_enabled() -> bool:
    v = (os.getenv("VM_LANGUAGE_INTELLIGENCE") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def is_analysis_only() -> bool:
    v = (os.getenv("VM_LANGUAGE_INTELLIGENCE_ANALYSIS") or "0").strip().lower()
    return v in ("1", "true", "yes", "on", "only")


def fast_mode_budget_ms() -> float:
    raw = (os.getenv("VM_LI_MAX_MS_PER_SEGMENT") or "40").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 40.0


def version() -> str:
    return "2.0"
