"""AI Core — the single intelligent core (director) of TubeDub.

Public API::

    from engines.ai_core import get_ai_core, current_ai_core
    core = get_ai_core(task_id)
    core.analyze(...); core.plan(...); core.adapt_segments(...)

Submodules:
    llm_gateway      — the ONLY entry point for LLM chat calls
    project_analysis — whole-project understanding (ProjectProfile)
    strategy         — decisions for the project (ProjectStrategy)
    voice_director   — AI-decided voice delivery
    core             — AICore facade + per-task registry
    report           — OpenDDF "AI Core Report" builder
"""

from __future__ import annotations

from engines.ai_core import llm_gateway  # noqa: F401
from engines.ai_core.core import (  # noqa: F401
    AICore,
    current_ai_core,
    get_ai_core,
    release_ai_core,
)
from engines.ai_core.project_analysis import ProjectProfile, analyze_project  # noqa: F401
from engines.ai_core.strategy import ProjectStrategy, build_strategy  # noqa: F401
from engines.ai_core.voice_director import VoiceDirection, decide_voice  # noqa: F401
from engines.ai_core.contracts import AgentExecutionResult, PipelineResult, ProjectManifest  # noqa: F401
from engines.ai_core.orchestrator import AICoreOrchestrator  # noqa: F401
from engines.ai_core.planner_agent import PlannerAgent  # noqa: F401
from engines.ai_core.director_agent import DirectorAgent  # noqa: F401
from engines.ai_core.translation_agent import TranslationAgent  # noqa: F401
from engines.ai_core.semantic_agent import SemanticAgent  # noqa: F401
from engines.ai_core.timing_agent import TimingAgent  # noqa: F401
from engines.ai_core.grammar_agent import GrammarAgent  # noqa: F401
from engines.ai_core.quality_agent import QualityAgent  # noqa: F401
from engines.ai_core.reviewer_agent import ReviewerAgent  # noqa: F401
from engines.ai_core.voice_preparation_agent import VoicePreparationAgent  # noqa: F401
from engines.ai_core.voice_agent import VoiceAgent  # noqa: F401
from engines.ai_core.voice_quality_agent import VoiceQualityAgent  # noqa: F401
from engines.ai_core.voice_verification_agent import VoiceVerificationAgent  # noqa: F401
from engines.ai_core.mix_agent import MixAgent  # noqa: F401

__all__ = [
    "AICore",
    "get_ai_core",
    "current_ai_core",
    "release_ai_core",
    "ProjectProfile",
    "analyze_project",
    "ProjectStrategy",
    "build_strategy",
    "VoiceDirection",
    "decide_voice",
    "llm_gateway",
    "AgentExecutionResult",
    "ProjectManifest",
    "PlannerAgent",
    "DirectorAgent",
    "TranslationAgent",
    "SemanticAgent",
    "TimingAgent",
    "GrammarAgent",
    "QualityAgent",
    "ReviewerAgent",
    "AICoreOrchestrator",
    "PipelineResult",
    "VoicePreparationAgent",
    "VoiceAgent",
    "VoiceQualityAgent",
    "VoiceVerificationAgent",
    "MixAgent",
]
