"""DSAL public API."""

from engines.dsal.benchmark import run_dsal_benchmark
from engines.dsal.block_merge import (
    apply_semantic_block_merges,
    detect_block_candidates,
)
from engines.dsal.clause_coverage import (
    compute_clause_coverage,
    restore_missing_clauses,
)
from engines.dsal.core import (
    DurationAnalysis,
    DSALResult,
    adapt_duration_semantic,
    analyze_duration,
    stamp_dsal_on_segment,
    strip_dsal_elaboration_fillers,
)
from engines.dsal.lock_gate import apply_lock_with_gate, evaluate_lock_gate
from engines.dsal.pre_lock_polish import apply_pre_lock_polish, polish_segments_before_lock
from engines.dsal.studio_editorial import (
    refresh_dsal_on_edits,
    refresh_dsal_on_segment,
    relock_after_editorial,
)

__all__ = [
    "DurationAnalysis",
    "DSALResult",
    "adapt_duration_semantic",
    "analyze_duration",
    "stamp_dsal_on_segment",
    "strip_dsal_elaboration_fillers",
    "compute_clause_coverage",
    "restore_missing_clauses",
    "detect_block_candidates",
    "apply_semantic_block_merges",
    "evaluate_lock_gate",
    "apply_lock_with_gate",
    "apply_pre_lock_polish",
    "polish_segments_before_lock",
    "refresh_dsal_on_segment",
    "refresh_dsal_on_edits",
    "relock_after_editorial",
    "run_dsal_benchmark",
]
