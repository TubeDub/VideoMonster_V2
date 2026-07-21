"""Dub Engine 2.0 — Master Spec Part 5.

Post–Semantic Lock audio planning, timing, scheduler, natural speech.
Independent of Translation Engine (contracts only).
"""

from __future__ import annotations

from engines.dub_engine_v2.adaptation_decision import (
    SKIP_FITS_NO_CHANGE,
    SKIP_TRANSLATION_LOCKED,
    ensure_skip_reason,
    finalize_segment_adaptation_fields,
    mark_adaptation_skipped,
    overflow_adaptation_violation,
)
from engines.dub_engine_v2.decision_trace import (
    format_decision_trace_openddf,
    ensure_decision_trace_complete,
)
from engines.dub_engine_v2.engine import DubEngineResult, run_dub_engine
from engines.dub_engine_v2.models import (
    AudioPlan,
    AudioUnitV2,
    DubMetrics,
    ProjectTimeline,
    SpeechUnitV2,
)
from engines.dub_engine_v2.overflow_strategy import (
    STRATEGY_COSTS,
    STRATEGY_ORDER,
    UnhandledOverflowError,
    assert_pipeline_may_succeed,
    decide_overflow,
)
from engines.dub_engine_v2.timing import ATO_ORDER

__all__ = [
    "ATO_ORDER",
    "SKIP_FITS_NO_CHANGE",
    "SKIP_TRANSLATION_LOCKED",
    "STRATEGY_COSTS",
    "STRATEGY_ORDER",
    "AudioPlan",
    "AudioUnitV2",
    "DubEngineResult",
    "DubMetrics",
    "ProjectTimeline",
    "SpeechUnitV2",
    "UnhandledOverflowError",
    "assert_pipeline_may_succeed",
    "decide_overflow",
    "ensure_decision_trace_complete",
    "ensure_skip_reason",
    "finalize_segment_adaptation_fields",
    "format_decision_trace_openddf",
    "mark_adaptation_skipped",
    "overflow_adaptation_violation",
    "run_dub_engine",
]
