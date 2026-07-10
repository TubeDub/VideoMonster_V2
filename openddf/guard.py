"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0 — Snapshot integrity guard.
"""

from __future__ import annotations

import inspect
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from openddf.diff import DiffAnalyzer
from openddf.exceptions import StageSnapshotIntegrityError
from openddf.timeline import TimelineTracker

_SKIP_STACK_PARTS = (
    "openddf/guard.py",
    "openddf\\guard.py",
    "contextlib.py",
    "site-packages",
    "/lib/python",
    "\\lib\\python",
    "runpy",
    "_pytest",
    "pytest",
)


def _get_caller_location() -> dict[str, Any]:
    """Find first user-code frame outside guard/contextlib/stdlib."""
    for frame_info in inspect.stack()[1:]:
        if frame_info.filename.startswith("<"):
            continue
        full = frame_info.filename.replace("\\", "/")
        if full.endswith("/openddf/guard.py") or full.endswith("\\openddf\\guard.py"):
            continue
        if "contextlib.py" in full:
            continue
        if "/site-packages/" in full or "\\site-packages\\" in full:
            continue
        if full.endswith("runpy.py") or "/runpy.py" in full:
            continue
        if frame_info.function in {"__enter__", "__exit__", "_get_caller_location"}:
            continue
        return {
            "file": Path(frame_info.filename).name,
            "line": frame_info.lineno,
            "function": frame_info.function,
            "full_path": full,
        }
    return {"file": "unknown", "line": 0, "function": "unknown"}


class SnapshotGuard:
    """Context manager that detects disallowed mutations on a target mapping."""

    def __init__(
        self,
        target: dict[str, Any],
        allowed_mutations: Iterable[str],
        context_tracker: TimelineTracker | None = None,
    ) -> None:
        self.target = target
        self.allowed_mutations = set(allowed_mutations)
        self.context_tracker = context_tracker
        self._snapshot: dict[str, Any] | None = None

    def __enter__(self) -> SnapshotGuard:
        self._snapshot = deepcopy(self.target)
        if self.context_tracker is not None:
            self.context_tracker.add_event("snapshot_guard_enter", "OK")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            return False

        assert self._snapshot is not None
        diff = DiffAnalyzer.compute_diff(self._snapshot, self.target)
        forbidden = [
            change
            for change in diff.get("changes", [])
            if change.get("field") not in self.allowed_mutations
        ]

        if not forbidden:
            if self.context_tracker is not None:
                self.context_tracker.add_event("snapshot_guard_exit", "OK")
            return False

        first = forbidden[0]
        location = _get_caller_location()
        if self.context_tracker is not None:
            self.context_tracker.add_event(
                "snapshot_integrity_violation",
                "FAILED",
                {"field": first.get("field"), "location": location},
            )

        raise StageSnapshotIntegrityError(
            f"Disallowed mutation of field {first.get('field')!r}",
            field_name=str(first.get("field") or ""),
            old_value=first.get("old_value"),
            new_value=first.get("new_value"),
            allowed_mutations=sorted(self.allowed_mutations),
            location_info=location,
        )
