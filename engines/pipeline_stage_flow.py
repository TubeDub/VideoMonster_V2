"""Pipeline stage transition logging — orchestration only."""

from __future__ import annotations

import logging

logger = logging.getLogger("tubedub.pipeline_stage_flow")


def log_stage_begin(task_id: str, stage: str) -> None:
    logger.info("[PipelineFlow] task=%s BEGIN %s", task_id or "?", stage)


def log_stage_end(task_id: str, stage: str) -> None:
    logger.info("[PipelineFlow] task=%s END %s", task_id or "?", stage)


def log_stage_transition(task_id: str, from_stage: str, to_stage: str) -> None:
    logger.info(
        "[PipelineFlow] task=%s %s -> %s",
        task_id or "?",
        from_stage,
        to_stage,
    )
