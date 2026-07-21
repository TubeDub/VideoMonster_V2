"""Translation Quality Engine (TQE) — industrial QA gate before TTS.

Public API:
  run_tqe_gate(...) -> TQEBatchResult
  filter_tts_texts(...)
"""

from __future__ import annotations

from engines.tqe.models import (
    ConfidenceMetrics,
    QualityReport,
    ReviewStatus,
    SegmentQualityDecision,
    TQEBatchResult,
)
from engines.tqe.pipeline import filter_tts_texts, run_tqe_on_segments

__all__ = [
    "ConfidenceMetrics",
    "QualityReport",
    "ReviewStatus",
    "SegmentQualityDecision",
    "TQEBatchResult",
    "run_tqe_gate",
    "run_tqe_on_segments",
    "filter_tts_texts",
]


def run_tqe_gate(
    *,
    task_id: str,
    originals: list[str],
    translations: list[str],
    timing_map: list | None = None,
    app_dir: str | None = None,
    confidence_threshold: float | None = None,
    persist: bool = True,
    allow_retry: bool = True,
) -> TQEBatchResult:
    """Hard gate: no segment reaches TTS unless TQE allows it."""
    return run_tqe_on_segments(
        task_id=task_id,
        originals=originals,
        translations=translations,
        timing_map=timing_map,
        app_dir=app_dir,
        confidence_threshold=confidence_threshold,
        persist=persist,
        allow_retry=allow_retry,
    )
