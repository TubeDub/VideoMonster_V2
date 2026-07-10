"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0 — Diff analyzer.
"""

from __future__ import annotations

from typing import Any


class DiffAnalyzer:
    """Computes flat differences between two dictionary states."""

    @staticmethod
    def compute_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        before = before or {}
        after = after or {}
        all_keys = set(before.keys()) | set(after.keys())

        changes: list[dict[str, Any]] = []
        added: dict[str, Any] = {}
        removed: dict[str, Any] = {}

        for key in sorted(all_keys):
            in_before = key in before
            in_after = key in after
            if in_before and in_after:
                old_val = before[key]
                new_val = after[key]
                if old_val != new_val:
                    changes.append(
                        {
                            "field": key,
                            "old_value": old_val,
                            "new_value": new_val,
                            "change_type": "modified",
                        }
                    )
            elif in_after:
                added[key] = after[key]
                changes.append(
                    {
                        "field": key,
                        "old_value": None,
                        "new_value": after[key],
                        "change_type": "added",
                    }
                )
            else:
                removed[key] = before[key]
                changes.append(
                    {
                        "field": key,
                        "old_value": before[key],
                        "new_value": None,
                        "change_type": "removed",
                    }
                )

        return {
            "changes": changes,
            "added": added,
            "removed": removed,
            "change_count": len(changes),
        }
