"""Translation Pipeline Simplification (TPS) / Translation Fast Path v2.

Public API:
  run_tps_pipeline(...) — Fast → Retry(1) → Judge → Manual
  approve_segment / get_approved_text / guard_post_pass_mutation
"""

from __future__ import annotations

from engines.tps.version import TPS_PIPELINE_VERSION

from engines.tps.approved_text import (
    ApprovedTextMutationError,
    approve_segment,
    approved_texts_from_segments,
    get_approved_text,
    guard_post_pass_mutation,
    is_translation_locked,
    sync_audits_approved,
)
from engines.tps.fast_qa import FastQAResult, run_fast_qa
from engines.tps.metrics import TPSMetrics, write_tps_metrics
from engines.tps.owners import DualWriterError, get_owner_registry
from engines.tps.pipeline import TPSBatchResult, run_tps_pipeline
from engines.tps.duration_stamp import stamp_duration_after_approved
from engines.tps.statuses import TPSPath, TQEStatus

__all__ = [
    "TPS_PIPELINE_VERSION",
    "TQEStatus",
    "TPSPath",
    "FastQAResult",
    "run_fast_qa",
    "run_tps_pipeline",
    "TPSBatchResult",
    "TPSMetrics",
    "write_tps_metrics",
    "approve_segment",
    "get_approved_text",
    "approved_texts_from_segments",
    "guard_post_pass_mutation",
    "is_translation_locked",
    "sync_audits_approved",
    "stamp_duration_after_approved",
    "ApprovedTextMutationError",
    "DualWriterError",
    "get_owner_registry",
]
