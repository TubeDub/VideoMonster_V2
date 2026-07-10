"""Smart router — map failure type to responsible agent."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.ai_core.quality_agent.router")

_FAILURE_TO_AGENT: dict[str, str] = {
    "entity": "TranslationAgent",
    "terminology": "TranslationAgent",
    "language": "TranslationAgent",
    "meaning": "SemanticAgent",
    "timing": "TimingAgent",
    "slot_fit": "TimingAgent",
    "grammar": "GrammarAgent",
    "syntax": "GrammarAgent",
    "natural_speech": "GrammarAgent",
    "sentence_integrity": "GrammarAgent",
    "voice_readiness": "GrammarAgent",
}


def agent_for_failure(failure_type: str | None) -> str | None:
    if not failure_type:
        return None
    return _FAILURE_TO_AGENT.get(failure_type)


def route_and_fix_segment(
    segment: dict,
    failure_type: str | None,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    *,
    segment_index: int,
) -> tuple[dict, str | None]:
    """
    Route failure to responsible agent and re-run ONLY that segment.

    Returns updated segment and agent name routed to.
    """
    agent_name = agent_for_failure(failure_type)
    if not agent_name:
        logger.debug("No agent route for failure_type=%s", failure_type)
        return segment, None

    from engines.ai_core.quality_agent.retry_orchestrator import rerun_agent_for_segment

    updated = rerun_agent_for_segment(
        agent_name,
        segment_index,
        manifest,
        state,
        task_id,
    )
    return updated, agent_name
