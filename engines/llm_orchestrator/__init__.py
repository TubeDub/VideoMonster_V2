"""LLM Orchestrator — multi-model dispatch for TubeDub.

Quality-first model routing: short/simple segments may use a lighter model,
complex segments (names, idioms, long context) use a stronger model. Backup
models are used only on timeout/low-confidence — never on every segment.

Integrates with existing circuit breakers in ``engines.translation_adapt`` and
does not bypass quality gates.
"""

from __future__ import annotations

from engines.llm_orchestrator.model_pool import (
    LLMModelInfo,
    LLMModelPool,
    ModelTier,
    get_model_pool,
)
from engines.llm_orchestrator.orchestrator import (
    LLMOrchestrator,
    LLMTask,
    LLMTaskResult,
    get_llm_orchestrator,
)
from engines.llm_orchestrator.router import (
    SegmentDifficulty,
    assess_segment_difficulty,
    route_segment,
)

__all__ = [
    "LLMModelInfo",
    "LLMModelPool",
    "ModelTier",
    "get_model_pool",
    "LLMOrchestrator",
    "LLMTask",
    "LLMTaskResult",
    "get_llm_orchestrator",
    "SegmentDifficulty",
    "assess_segment_difficulty",
    "route_segment",
]
