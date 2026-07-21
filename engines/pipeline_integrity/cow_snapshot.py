"""Copy-on-Write Snapshot — MASTER TZ v3.0 P25.

Stages must never mutate the input snapshot in place.
Work on a deep copy; commit only after Validate Output.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from engines.pipeline_integrity.guards import StageSnapshotGuard


@dataclass
class StageSnapshot:
    """Frozen deep copy of segments at stage entry."""

    stage: str
    segments: list[dict[str, Any]]

    def clone_for_write(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.segments)


@dataclass
class CowStageContext:
    """P25 context: immutable input + writable working copy."""

    stage: str
    input_snapshot: StageSnapshot
    working: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def begin(
        cls,
        stage: str,
        segments: list[dict[str, Any]],
    ) -> "CowStageContext":
        snap = StageSnapshot(stage=stage, segments=copy.deepcopy(segments))
        working = copy.deepcopy(segments)
        return cls(stage=stage, input_snapshot=snap, working=working)

    def commit(self) -> list[dict[str, Any]]:
        return self.working

    def validate_against_contract(self, *, mutator_module: str | None = None) -> None:
        StageSnapshotGuard.check(
            self.input_snapshot.segments,
            self.working,
            stage=self.stage,
            mutator_module=mutator_module,
        )


def apply_cow_result(
    original: list[dict[str, Any]],
    working: list[dict[str, Any]],
) -> None:
    """Replace original list contents with committed working copy (atomic swap)."""
    original[:] = working
