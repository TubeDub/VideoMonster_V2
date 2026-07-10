"""Smart Translation Router (STR) configuration."""

from __future__ import annotations

import os

STR_VERSION = 1
KNOWLEDGE_BASE_FILE = "str_knowledge_base.json"

# Quality thresholds (aligned with translation_quality_score)
MIN_ACCEPT_QUALITY = 55.0
MIN_QUALITY_GOOD = 70.0
MIXED_LANGUAGE_WARN = 12.0

# Max engines to try sequentially on poor quality
MAX_ENGINE_TRIES = 4

# When score is doubtful (accept but not good), compare top-N engines
COMPARE_DOUBTFUL = True
COMPARE_TOP_N = 3
DOUBTFUL_SCORE_LOW = 55.0
DOUBTFUL_SCORE_HIGH = 72.0

# Self-diagnostics: rolling window for trend detection
TREND_WINDOW = 20
DEGRADATION_DROP = 8.0


def use_str() -> bool:
    """Enable Smart Translation Router instead of stable Marian / legacy router."""
    v = (os.getenv("VM_USE_STR") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def compare_doubtful_enabled() -> bool:
    v = (os.getenv("VM_STR_COMPARE") or "1").strip().lower()
    return COMPARE_DOUBTFUL and v not in ("0", "false", "no", "off")


def max_engine_tries() -> int:
    raw = (os.getenv("VM_STR_MAX_TRIES") or "").strip()
    if raw.isdigit():
        return max(1, min(8, int(raw)))
    return MAX_ENGINE_TRIES
