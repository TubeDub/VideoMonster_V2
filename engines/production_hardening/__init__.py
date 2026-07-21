"""Production Hardening — TZ v2 P16.

Long-run / stress / fault-injection / resource / release checklist foundations.
CI runs fast modes; full 8h/24h harnesses are opt-in via CLI flags.
"""

from __future__ import annotations

from engines.production_hardening.checklist import run_release_checklist
from engines.production_hardening.enriched_logging import build_error_record
from engines.production_hardening.fault_injection import run_fault_suite
from engines.production_hardening.resource_manager import (
    ResourceSnapshot,
    cleanup_temp_wavs,
    take_resource_snapshot,
)

__all__ = [
    "ResourceSnapshot",
    "build_error_record",
    "cleanup_temp_wavs",
    "run_fault_suite",
    "run_release_checklist",
    "take_resource_snapshot",
]
