"""AI Core 3.0 — multi-agent layer.

AI Core is a coordinator; the real work is done by single-responsibility agents
that reuse the existing engines. Public API::

    from engines.ai_core.agents import AgentCoordinator, SegmentContext
    coord = AgentCoordinator(task_id, profile_dict, strategy_dict)
    texts, records = coord.run(segments, timing_map, sources, src_lang=..., tgt_lang=...)
"""

from __future__ import annotations

from engines.ai_core.agents.agents_meta import (  # noqa: F401
    MixAgent,
    PlannerAgent,
    VoiceAgent,
)
from engines.ai_core.agents.agents_text import (  # noqa: F401
    EntityAgent,
    GrammarAgent,
    QualityAgent,
    SemanticAgent,
    TimingAgent,
    TranslationAgent,
)
from engines.ai_core.agents.base import (  # noqa: F401
    Agent,
    AgentCache,
    AgentResult,
    SegmentContext,
)
from engines.ai_core.agents.coordinator import (  # noqa: F401
    TIMELINE_ORDER,
    AgentCoordinator,
)

__all__ = [
    "Agent",
    "AgentResult",
    "AgentCache",
    "SegmentContext",
    "AgentCoordinator",
    "TIMELINE_ORDER",
    "PlannerAgent",
    "TranslationAgent",
    "SemanticAgent",
    "EntityAgent",
    "TimingAgent",
    "GrammarAgent",
    "QualityAgent",
    "VoiceAgent",
    "MixAgent",
]
