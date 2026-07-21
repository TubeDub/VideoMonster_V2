"""Reference Audio Sync Monitor (RASM) — dual playback QC for dubbed audio.

Phases: R0 dual playback → R1 metrics → R2 detectors → R3 timeline → R4 reports → R5 hooks.
"""

from __future__ import annotations

from engines.rasm.analyze import analyze_project
from engines.rasm.compare import compare_sync_reports
from engines.rasm.config import RasmSettings, default_settings, load_rasm_settings
from engines.rasm.metrics import (
    SegmentSyncMetrics,
    analyze_segments,
    compute_segment_metrics,
    compute_stats,
)
from engines.rasm.reports import write_sync_reports

__all__ = [
    "RasmSettings",
    "SegmentSyncMetrics",
    "analyze_project",
    "analyze_segments",
    "compare_sync_reports",
    "compute_segment_metrics",
    "compute_stats",
    "default_settings",
    "load_rasm_settings",
    "write_sync_reports",
]
