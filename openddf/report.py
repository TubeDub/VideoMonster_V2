"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0 — Recovery hint generator.
"""

from __future__ import annotations

from typing import Any

from openddf.exceptions import DDFError, StageSnapshotIntegrityError


class RecoveryHintGenerator:
    """Structured root-cause analysis and remediation hints."""

    @staticmethod
    def generate_hint(exc_val: Exception) -> dict[str, Any]:
        if isinstance(exc_val, StageSnapshotIntegrityError):
            loc = exc_val.location_info or {}
            file_name = loc.get("file", "unknown")
            line_no = loc.get("line", 0)
            function = loc.get("function", "unknown")
            field = exc_val.field_name or "?"
            allowed = ", ".join(exc_val.allowed_mutations) or "(none)"
            root_cause = (
                f"Field '{field}' was mutated from {exc_val.old_value!r} "
                f"to {exc_val.new_value!r} outside allowed mutations."
            )
            recommendation = (
                f"Stop writing to '{field}' in {file_name}:{line_no} ({function}), "
                f"or add '{field}' to allowed_mutations. "
                f"Currently allowed: {allowed}."
            )
            return {
                "root_cause": root_cause,
                "recommendation": recommendation,
                "field_name": field,
                "location": loc,
            }

        if isinstance(exc_val, DDFError):
            return {
                "root_cause": str(exc_val),
                "recommendation": "Review OpenDDF diagnostic archive for details.",
            }

        return {
            "root_cause": f"{type(exc_val).__name__}: {exc_val}",
            "recommendation": "Inspect stacktrace.txt and pipeline.log in the diagnostic ZIP.",
        }
