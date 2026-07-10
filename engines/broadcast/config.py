"""Broadcast-grade pipeline configuration."""

from __future__ import annotations

import os

BROADCAST_VERSION = 1
INCIDENTS_LOG = "broadcast_incidents.log"
REPORT_FILE = "broadcast_quality_report.json"

# Token format: [##123##]
TOKEN_PREFIX = "[##"
TOKEN_SUFFIX = "##]"


def use_broadcast_pipeline() -> bool:
    v = (os.getenv("VM_BROADCAST_PIPELINE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def strict_gate() -> bool:
    v = (os.getenv("VM_BROADCAST_STRICT_GATE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def tournament_max_engines() -> int:
    raw = (os.getenv("VM_BROADCAST_MAX_ENGINES") or "5").strip()
    if raw.isdigit():
        return max(1, min(8, int(raw)))
    return 5


def block_on_corruption() -> bool:
    """If True, unrecoverable segment → FAILED (no silent pass-through)."""
    v = (os.getenv("VM_BROADCAST_BLOCK_FAILED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")
