"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0 — Public API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openddf.diff import DiffAnalyzer
from openddf.dumper import DiagnosticDumper, __version__
from openddf.environment import collect_environment_info
from openddf.exceptions import DDFError, StageSnapshotIntegrityError
from openddf.guard import SnapshotGuard
from openddf.report import RecoveryHintGenerator
from openddf.timeline import TimelineTracker
from openddf.utils import REDACTED, filter_sensitive_data

__all__ = [
    "DDFError",
    "DiagnosticContext",
    "DiagnosticDumper",
    "DiffAnalyzer",
    "RecoveryHintGenerator",
    "SnapshotGuard",
    "StageSnapshotIntegrityError",
    "TimelineTracker",
    "REDACTED",
    "__version__",
    "collect_environment_info",
    "filter_sensitive_data",
]


class DiagnosticContext:
    """Session orchestrator: timeline tracking and crash archive on failure."""

    def __init__(self, run_id: str, output_dir: str | Path) -> None:
        self.run_id = run_id
        self.output_dir = Path(output_dir)
        self.timeline = TimelineTracker()
        self._dumper = DiagnosticDumper(self.output_dir, run_id)
        self.last_archive_path: str | None = None
        self._last_snapshots: tuple[dict[str, Any] | None, dict[str, Any] | None] = (None, None)

    def register_snapshots(
        self,
        snapshot_before: dict[str, Any] | None = None,
        snapshot_after: dict[str, Any] | None = None,
    ) -> None:
        """Optional hook to attach before/after states to crash dumps."""
        self._last_snapshots = (snapshot_before, snapshot_after)

    def __enter__(self) -> DiagnosticContext:
        self.timeline.add_event("diagnostic_session_start", "OK", {"run_id": self.run_id})
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            self.timeline.add_event("diagnostic_session_end", "OK")
            return False

        self.timeline.add_event(
            "crash",
            "FAILED",
            {"exception_type": getattr(exc_type, "__name__", "Exception")},
        )
        before, after = self._last_snapshots
        if isinstance(exc_val, StageSnapshotIntegrityError) and before is None:
            before = {
                exc_val.field_name: exc_val.old_value,
            }
            after = {
                exc_val.field_name: exc_val.new_value,
            }
        self.last_archive_path = self._dumper.dump_crash(
            exc_val,
            self.timeline,
            snapshot_before=before,
            snapshot_after=after,
        )
        return False
