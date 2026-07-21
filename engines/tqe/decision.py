"""Decision Engine — final PASS/REJECT for TTS eligibility."""

from __future__ import annotations

import os
from typing import Any

from engines.tqe.models import (
    ConfidenceMetrics,
    QualityReport,
    ReviewStatus,
    RetryStrategyName,
    SegmentQualityDecision,
)


def _default_threshold() -> float:
    try:
        return float(os.getenv("TQE_CONFIDENCE_THRESHOLD", "0.72"))
    except ValueError:
        return 0.72


def merge_confidence(reports: list[QualityReport]) -> ConfidenceMetrics:
    keys = (
        "entity_preservation",
        "meaning_coverage",
        "grammar_integrity",
        "sentence_completeness",
        "narrative_integrity",
        "timing_fitness",
    )
    vals = {k: [] for k in keys}
    for r in reports:
        if not r.confidence:
            continue
        for k in keys:
            vals[k].append(float(getattr(r.confidence, k)))
    return ConfidenceMetrics(
        **{k: (sum(v) / len(v) if v else 1.0) for k, v in vals.items()}
    )


def decide_segment(
    *,
    index: int,
    original: str,
    translation: str,
    reports: list[QualityReport],
    threshold: float | None = None,
) -> SegmentQualityDecision:
    """HF4: Quality Score / confidence is NOT the sole Reject criterion.

    REJECT only on critical reviewer signals (or explicit REJECT reports).
    Low overall confidence alone → soft WARN (still PASS for TTS routing),
    so score 72 can pass and score 85 with entity_missing still fails.
    """
    thr = _default_threshold() if threshold is None else float(threshold)
    conf = merge_confidence(reports)
    overall = conf.overall()

    rejects = [r for r in reports if r.status == ReviewStatus.REJECT]
    # Critical codes that alone justify REJECT (multi-factor, not score)
    _CRITICAL = {
        "entity_missing",
        "english_leak",
        "mixed_language",
        "incomplete",
        "incomplete_sentence",
        "dirty_mt_noop",
        "meaning_loss",
        "severe_truncation",
        "clause_coverage",
        "broken_phrase",
        "ssml_in_text",
        "empty",
    }
    critical_hits: list[str] = []
    for r in reports:
        for e in r.errors or []:
            code = str(e.get("code") or "")
            sev = str(e.get("severity") or "").lower()
            if code in _CRITICAL or sev == "critical":
                critical_hits.append(code or sev)

    # Also treat dirty Raw==Naturalized as critical if stamped on report
    for r in reports:
        if any(
            str(e.get("code") or "") == "dirty_mt_noop"
            for e in (r.errors or [])
        ):
            if "dirty_mt_noop" not in critical_hits:
                critical_hits.append("dirty_mt_noop")

    status = ReviewStatus.REJECT if (rejects or critical_hits) else ReviewStatus.PASS
    soft_low_score = False
    if status == ReviewStatus.PASS and overall < thr:
        # Soft signal only — do NOT reject on score alone (TZ Part 4)
        soft_low_score = True

    # Prefer most specific retry strategy from first rejector
    strategy = RetryStrategyName.NONE.value
    for r in rejects:
        if r.retry_strategy and r.retry_strategy != RetryStrategyName.NONE.value:
            strategy = r.retry_strategy
            break
    if status == ReviewStatus.REJECT and strategy == RetryStrategyName.NONE.value:
        strategy = RetryStrategyName.MEANING_PRESERVATION.value

    parts: list[str] = []
    for r in rejects:
        if r.explanation:
            parts.append(f"{r.reviewer_name}: {r.explanation}")
        for e in r.errors:
            tok = e.get("token") or e.get("detail") or ""
            parts.append(f"{r.reviewer_name}/{e.get('code')}: {tok}".strip(": "))
    for code in critical_hits:
        if code not in " ".join(parts):
            parts.append(f"critical:{code}")
    if soft_low_score:
        parts.append(
            f"soft:low_confidence {overall:.2f} < {thr:.2f} (not sole reject)"
        )

    allowed = status == ReviewStatus.PASS
    return SegmentQualityDecision(
        index=index,
        status=status,
        overall_confidence=overall,
        reports=reports,
        explanation="; ".join(parts) or "PASS",
        retry_strategy=strategy if not allowed else RetryStrategyName.NONE.value,
        original=original,
        translation=translation,
        allowed_for_tts=allowed,
    )
