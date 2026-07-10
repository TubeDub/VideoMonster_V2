"""Atomic commit / rollback contract (TZ §1.5)."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from engines.pipeline_integrity.exceptions import PipelineIntegrityError

logger = logging.getLogger("tubedub.pipeline_integrity.rollback")


@dataclass
class StageRollbackFrame:
    stage: str
    segments_data: list[dict[str, Any]]
    task_info_fragment: dict[str, Any] = field(default_factory=dict)


class StageTransaction:
    """
    Snapshot segments + selected task_info keys before a stage.
    On PipelineIntegrityError — restore prior state (Atomic Commit).
    """

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self._frame: StageRollbackFrame | None = None
        self.committed = False

    def begin(
        self,
        segments_data: list[dict[str, Any]],
        task_info: dict[str, Any] | None = None,
        *,
        keys: tuple[str, ...] = ("tts_files",),
    ) -> None:
        fragment = {}
        if task_info:
            for k in keys:
                if k in task_info:
                    fragment[k] = copy.deepcopy(task_info[k])
        self._frame = StageRollbackFrame(
            stage=self.stage,
            segments_data=copy.deepcopy(segments_data),
            task_info_fragment=fragment,
        )

    def commit(self) -> None:
        self.committed = True
        self._frame = None

    def rollback(
        self,
        segments_data: list[dict[str, Any]],
        task_info: dict[str, Any] | None = None,
    ) -> None:
        if self._frame is None:
            return
        segments_data[:] = copy.deepcopy(self._frame.segments_data)
        if task_info and self._frame.task_info_fragment:
            for k, v in self._frame.task_info_fragment.items():
                task_info[k] = copy.deepcopy(v)
        logger.warning(
            "pipeline rollback stage=%s restored %d segments",
            self.stage,
            len(segments_data),
        )
        self._frame = None


def run_stage_atomic(
    stage: str,
    segments_data: list[dict[str, Any]],
    task_info: dict[str, Any] | None,
    fn: Callable[[], None],
) -> None:
    """Execute fn; rollback segments on PipelineIntegrityError."""
    tx = StageTransaction(stage)
    tx.begin(segments_data, task_info)
    try:
        fn()
        tx.commit()
    except PipelineIntegrityError:
        tx.rollback(segments_data, task_info)
        raise
