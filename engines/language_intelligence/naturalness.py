"""Naturalness Score 0–100 and tier-based action policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NaturalnessTier:
    score: float
    tier: str
    action: str  # skip | suggest | fix_if_confident | analyze


def tier_from_score(score: float) -> NaturalnessTier:
    s = max(0.0, min(100.0, float(score)))
    if s >= 95:
        return NaturalnessTier(s, "ideal", "skip")
    if s >= 85:
        return NaturalnessTier(s, "good", "skip")
    if s >= 70:
        return NaturalnessTier(s, "fair", "suggest")
    if s >= 50:
        return NaturalnessTier(s, "weak", "fix_if_confident")
    return NaturalnessTier(s, "poor", "analyze")


def should_attempt_fix(tier: NaturalnessTier, confidence: float) -> bool:
    """Whether fix may be applied given naturalness tier and confidence."""
    if tier.action == "skip":
        return False
    if tier.action == "suggest":
        return confidence >= 0.95
    if tier.action == "fix_if_confident":
        return confidence >= 0.85
    if tier.action == "analyze":
        return confidence >= 0.85
    return False


def aggregate_scores(scores: list[float]) -> float:
    if not scores:
        return 100.0
    return round(sum(scores) / len(scores), 1)


def four_questions_ok(
    *,
    naturalness: NaturalnessTier,
    has_objective_issue: bool,
    confidence: float,
    semantic_ok: bool,
) -> tuple[bool, list[str]]:
    """
    Four mandatory questions before any change.
    Returns (all_ok, reasons_if_not).
    """
    reasons: list[str] = []
    if naturalness.tier in ("ideal", "good"):
        reasons.append("natural_enough")
    if not has_objective_issue:
        reasons.append("no_objective_issue")
    if not should_attempt_fix(naturalness, confidence):
        reasons.append("confidence_or_tier_block")
    if not semantic_ok:
        reasons.append("semantic_validation_failed")
    ok = not reasons
    return ok, reasons
