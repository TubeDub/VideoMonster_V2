"""P416 Realtime Conflict Resolver — escalate to Decision Layer (no silent fix)."""

from __future__ import annotations

from typing import Any

from engines.decision_policy import run_decision_policy
from engines.dub_engine_v2.models import ProjectTimeline
from engines.semantic_v3.types import SemanticSentence


def resolve_conflicts_via_decision(
    sentences: list[SemanticSentence],
    conflicts: list[dict[str, Any]],
    *,
    profile: str = "",
) -> dict[str, Any]:
    """
    Do not patch timing directly.
    Re-run Decision Policy so it chooses the next strategy.
    """
    if not conflicts:
        return {"escalated": False, "conflicts": []}
    graph = run_decision_policy(sentences, profile=profile, attach=True)
    return {
        "escalated": True,
        "conflicts": conflicts,
        "decision_graph": graph.to_dict(),
    }


def attach_conflicts_to_timeline(
    timeline: ProjectTimeline,
    conflicts: list[dict[str, Any]],
) -> ProjectTimeline:
    timeline.conflicts = list(conflicts)
    return timeline
