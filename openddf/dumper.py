"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0 — Diagnostic archive dumper.
"""

from __future__ import annotations

import json
import shutil
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openddf.diff import DiffAnalyzer
from openddf.environment import collect_environment_info
from openddf.exceptions import StageSnapshotIntegrityError
from openddf.report import RecoveryHintGenerator
from openddf.timeline import TimelineTracker
from openddf.utils import filter_sensitive_data

__version__ = "0.1.0"


class DiagnosticDumper:
    """Serializes crash evidence into a flat diagnostic ZIP archive."""

    def __init__(self, output_dir: str | Path, run_id: str) -> None:
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.diagnostics_root = self.output_dir / "diagnostics"
        self.diagnostics_root.mkdir(parents=True, exist_ok=True)

    def dump_crash(
        self,
        exception: Exception,
        timeline: TimelineTracker,
        snapshot_before: dict[str, Any] | None = None,
        snapshot_after: dict[str, Any] | None = None,
    ) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        temp_dir = self.diagnostics_root / f"diagnostic_{self.run_id}_{ts}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            (temp_dir / "pipeline.log").write_text(timeline.export_text(), encoding="utf-8")
            (temp_dir / "stacktrace.txt").write_text(
                traceback.format_exc() or f"{type(exception).__name__}: {exception}",
                encoding="utf-8",
            )

            environment = collect_environment_info()
            (temp_dir / "environment.json").write_text(
                json.dumps(environment, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            recovery = RecoveryHintGenerator.generate_hint(exception)
            report = self._build_report(
                exception=exception,
                timeline=timeline,
                recovery=recovery,
                snapshot_before=snapshot_before,
                snapshot_after=snapshot_after,
            )
            (temp_dir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            if snapshot_before is not None and snapshot_after is not None:
                safe_before = filter_sensitive_data(snapshot_before)
                safe_after = filter_sensitive_data(snapshot_after)
                diff_payload = filter_sensitive_data(
                    DiffAnalyzer.compute_diff(snapshot_before, snapshot_after)
                )
                (temp_dir / "snapshot_before.json").write_text(
                    json.dumps(safe_before, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                (temp_dir / "snapshot_after.json").write_text(
                    json.dumps(safe_after, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                (temp_dir / "snapshot_diff.json").write_text(
                    json.dumps(diff_payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )

            zip_path = self.diagnostics_root / f"diagnostic_{self.run_id}.zip"
            if zip_path.is_file():
                zip_path.unlink()
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file_path in sorted(temp_dir.iterdir()):
                    if file_path.is_file():
                        zf.write(file_path, file_path.name)

            return str(zip_path.resolve())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _build_report(
        self,
        *,
        exception: Exception,
        timeline: TimelineTracker,
        recovery: dict[str, Any],
        snapshot_before: dict[str, Any] | None,
        snapshot_after: dict[str, Any] | None,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "framework": "OpenDDF",
            "version": __version__,
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exception": {
                "type": type(exception).__name__,
                "message": str(exception),
            },
            "recovery_hint": recovery,
            "timeline_event_count": len(timeline.get_events()),
            "snapshots_included": snapshot_before is not None and snapshot_after is not None,
        }

        if isinstance(exception, StageSnapshotIntegrityError):
            report["exception"].update(
                {
                    "field_name": exception.field_name,
                    "old_value": filter_sensitive_data(exception.old_value),
                    "new_value": filter_sensitive_data(exception.new_value),
                    "allowed_mutations": exception.allowed_mutations,
                    "location_info": exception.location_info,
                }
            )

        return filter_sensitive_data(report)
