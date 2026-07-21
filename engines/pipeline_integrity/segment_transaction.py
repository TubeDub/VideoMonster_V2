"""Segment Transaction — MASTER TZ v3.0 P26.

Begin → Validate Input → Execute → Validate Output → Commit
On error: Rollback (no partial segment state left behind).
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from engines.pipeline_integrity.cow_snapshot import CowStageContext, apply_cow_result
from engines.pipeline_integrity.exceptions import (
    ArchitectureViolation,
    PipelineIntegrityError,
)
from engines.pipeline_integrity.guards import StageSnapshotGuard
from engines.pipeline_integrity.rw_contract import (
    assert_write_allowed,
    stage_write_fields,
)

logger = logging.getLogger("tubedub.segment_transaction")


@dataclass
class SegmentTransaction:
    stage: str
    mutator_module: str = ""
    _ctx: CowStageContext | None = None
    _backup: list[dict[str, Any]] | None = None
    _committed: bool = False
    errors: list[str] = field(default_factory=list)

    def begin(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._backup = copy.deepcopy(segments)
        self._ctx = CowStageContext.begin(self.stage, segments)
        self._committed = False
        self.errors.clear()
        return self._ctx.working

    def validate_input(self, segments: list[dict[str, Any]] | None = None) -> None:
        rows = segments if segments is not None else (self._ctx.working if self._ctx else [])
        if not isinstance(rows, list):
            raise ArchitectureViolation(
                f"P26: invalid input for stage {self.stage}",
                stage=self.stage,
                rule="validate_input",
            )
        for i, seg in enumerate(rows):
            if not isinstance(seg, dict):
                raise ArchitectureViolation(
                    f"P26: segment {i} is not a dict",
                    stage=self.stage,
                    rule="validate_input",
                )

    def validate_output(self) -> None:
        if self._ctx is None:
            raise ArchitectureViolation(
                "P26: validate_output without begin",
                stage=self.stage,
                rule="validate_output",
            )
        self._ctx.validate_against_contract(mutator_module=self.mutator_module)

    def commit(self, original: list[dict[str, Any]]) -> None:
        if self._ctx is None:
            raise ArchitectureViolation(
                "P26: commit without begin",
                stage=self.stage,
                rule="commit",
            )
        self.validate_output()
        apply_cow_result(original, self._ctx.working)
        self._committed = True
        self._backup = None

    def rollback(self, original: list[dict[str, Any]]) -> None:
        if self._backup is not None:
            apply_cow_result(original, self._backup)
        self._committed = False
        logger.warning("P26: rolled back stage=%s", self.stage)

    def run(
        self,
        segments: list[dict[str, Any]],
        execute: Callable[[list[dict[str, Any]]], Any],
    ) -> Any:
        """Full transaction: begin → validate → execute → validate → commit."""
        working = self.begin(segments)
        try:
            self.validate_input(working)
            result = execute(working)
            self.validate_output()
            self.commit(segments)
            return result
        except Exception as exc:
            self.errors.append(str(exc))
            self.rollback(segments)
            raise


def stamp_field_via_contract(
    seg: dict[str, Any],
    field: str,
    value: Any,
    *,
    stage: str,
) -> None:
    """Write a field only if the stage R/W contract allows it."""
    assert_write_allowed(stage, field)
    seg[field] = value
