"""Professional Dubbing — configuration."""

from __future__ import annotations

import os

FILL_TARGET = 0.975
MAX_RATE_SLOW = -18
MIN_RATE_SLOW = -3
MAX_BREAK_MS = 350  # TZ v4.0 P2: SSML breaks capped 250–350ms



def is_enabled() -> bool:
    val = os.getenv("VM_PROFESSIONAL_DUBBING", "1").strip().lower()
    return val not in ("0", "false", "no", "off", "disabled")


def is_prosody_style(style_id: str | None, delivery: str = "") -> bool:
    sid = (style_id or "").strip().lower()
    deliv = (delivery or "").strip().lower()
    if sid in ("professional", "professional_dubbing", "modern", "documentary", "cinematic"):
        return True
    if "professional" in deliv or deliv in ("natural_modern", "calm_informative"):
        return True
    return os.getenv("VM_PROSODY_ALL_STYLES", "").strip().lower() in ("1", "true", "yes")
