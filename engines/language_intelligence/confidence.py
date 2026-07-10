"""Confidence system — never guess below threshold."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConfidenceDecision:
    value: float
    action: str  # apply | suggest | reject


def decide(confidence: float) -> ConfidenceDecision:
    c = max(0.0, min(1.0, float(confidence)))
    if c >= 1.0:
        return ConfidenceDecision(c, "apply")
    if c >= 0.95:
        return ConfidenceDecision(c, "apply")
    if c >= 0.85:
        return ConfidenceDecision(c, "apply_if_semantic_ok")
    if c >= 0.70:
        return ConfidenceDecision(c, "suggest")
    return ConfidenceDecision(c, "reject")


def may_apply(decision: ConfidenceDecision, *, semantic_ok: bool) -> bool:
    if decision.action == "apply":
        return True
    if decision.action == "apply_if_semantic_ok":
        return semantic_ok
    return False


def may_suggest(decision: ConfidenceDecision) -> bool:
    return decision.action == "suggest"
