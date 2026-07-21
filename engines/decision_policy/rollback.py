"""P311 Rollback Engine — next strategy when current fails (decision-only)."""

from __future__ import annotations

from engines.decision_policy.types import DecisionRecord, StrategyCandidate


def order_for_rollback(candidates: list[StrategyCandidate]) -> list[StrategyCandidate]:
    """Alive candidates sorted by decision_score desc, then cost asc."""
    alive = [c for c in candidates if not c.rejected]
    return sorted(alive, key=lambda c: (-c.decision_score, c.cost, c.label))


def select_with_rollback(
    candidates: list[StrategyCandidate],
    *,
    success_predicate=None,
) -> tuple[StrategyCandidate | None, list[str]]:
    """
    Try candidates in score order. success_predicate(candidate) -> bool.
    Default success = expected_fit.
    """
    path: list[str] = []
    pred = success_predicate or (lambda c: bool(c.expected_fit) or "manual_review" in c.steps)
    for cand in order_for_rollback(candidates):
        path.append(cand.label)
        if pred(cand):
            return cand, path
    # Fall back to best score even if not fit
    ordered = order_for_rollback(candidates)
    if ordered:
        return ordered[0], path + [f"fallback:{ordered[0].label}"]
    return None, path


def attach_rollback(record: DecisionRecord) -> DecisionRecord:
    accepted, path = select_with_rollback(record.candidates)
    record.rollback_path = path
    if accepted:
        record.accepted = accepted
        record.rejected = [c for c in record.candidates if c is not accepted]
        record.reason = (
            f"accepted={accepted.label}; score={accepted.decision_score}; "
            f"cost={accepted.cost}; path={'→'.join(path)}"
        )
    return record
