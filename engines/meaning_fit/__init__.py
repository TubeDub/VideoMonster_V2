"""Meaning Fit — duration-aware UK paraphrase (MF1–MF7)."""

from engines.meaning_fit.diagnostics import (
    apply_honest_meaning_fit_reasons,
    get_counters,
    reset_counters,
)
from engines.meaning_fit.duration_predictor import (
    DurationPrediction,
    classify_vs_slot,
    duration_gate,
    predict_ms,
    predict_vs_slot,
)
from engines.meaning_fit.exceptions import MeaningFitError, TruncateNotMeaningFitError
from engines.meaning_fit.flags import (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_BEFORE_LOCK,
    VM_FLAG_MEANING_FIT_EXPAND,
    VM_FLAG_MEANING_FIT_SHORTEN,
    ensure_meaning_fit_enabled_for_dubbing,
    list_mf1_flags,
    meaning_fit_before_lock_flag,
    meaning_fit_expand_flag,
    meaning_fit_flag,
    meaning_fit_shorten_flag,
)
from engines.meaning_fit.orchestrator import (
    MEANING_FIT_CALL_SITE,
    apply_meaning_fit_before_lock,
    fit_segment,
)
from engines.meaning_fit.score_select import score_variant, select_best
from engines.meaning_fit.semantic_expand import semantic_expand
from engines.meaning_fit.semantic_shorten import reject_chop_as_shorten, semantic_shorten
from engines.meaning_fit.skeleton import (
    fit_meaning,
    reject_truncate_as_success,
    skeleton_meaning_fit,
    wrap_meaning_text,
)
from engines.meaning_fit.types import FitRequest, FitResult, MeaningText

__all__ = [
    "MeaningFitError",
    "TruncateNotMeaningFitError",
    "VM_FLAG_MEANING_FIT",
    "VM_FLAG_MEANING_FIT_SHORTEN",
    "VM_FLAG_MEANING_FIT_EXPAND",
    "VM_FLAG_MEANING_FIT_BEFORE_LOCK",
    "list_mf1_flags",
    "ensure_meaning_fit_enabled_for_dubbing",
    "meaning_fit_flag",
    "meaning_fit_shorten_flag",
    "meaning_fit_expand_flag",
    "meaning_fit_before_lock_flag",
    "MeaningText",
    "FitRequest",
    "FitResult",
    "fit_meaning",
    "fit_segment",
    "reject_truncate_as_success",
    "reject_chop_as_shorten",
    "skeleton_meaning_fit",
    "wrap_meaning_text",
    "predict_ms",
    "predict_vs_slot",
    "classify_vs_slot",
    "duration_gate",
    "DurationPrediction",
    "semantic_shorten",
    "semantic_expand",
    "score_variant",
    "select_best",
    "apply_meaning_fit_before_lock",
    "MEANING_FIT_CALL_SITE",
    "apply_honest_meaning_fit_reasons",
    "get_counters",
    "reset_counters",
]
