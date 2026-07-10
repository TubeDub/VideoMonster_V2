"""Smart Segment Optimizer V2 — configuration."""

from __future__ import annotations

import os

SLOT_MARGIN_MS = 40

# Target fill of original segment duration (TZ §1).
FILL_TARGET_MIN = 0.95
FILL_TARGET_MAX = 1.00

# Skip optimization when estimate is within this band of segment duration.
FIT_BAND_MIN = FILL_TARGET_MIN
FIT_BAND_MAX = 1.03

# Stop shortening once estimate fits allowed window.
STOP_TOLERANCE = 1.0

MAX_LEVEL = int(os.getenv("VM_SSO_MAX_LEVEL", "5"))
# Allow up to 30% word removal while preserving meaning (was 0.78).
# Level 5 (subordinate clause) can safely reach 0.65–0.70 without semantic loss.
MIN_WORD_RETENTION = float(os.getenv("VM_SSO_MIN_WORD_RETENTION", "0.70"))


def is_enabled() -> bool:
    val = os.getenv("VM_SMART_SEGMENT_OPTIMIZER", "1").strip().lower()
    return val not in ("0", "false", "no", "off", "disabled")
