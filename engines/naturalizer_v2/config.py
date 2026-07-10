"""Naturalizer V2 configuration."""

from __future__ import annotations

import os

MIXED_LANGUAGE_RETRY_THRESHOLD = 3.0  # percent
QUALITY_RETRY_THRESHOLD = 62.0  # score below → retry


def is_v2_enabled() -> bool:
    v = (os.getenv("VM_NATURALIZER_V2") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def entity_mask_enabled() -> bool:
    v = (os.getenv("VM_NATURALIZER_ENTITY_MASK") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")
