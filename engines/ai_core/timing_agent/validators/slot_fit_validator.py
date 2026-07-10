"""Predicted duration vs slot_ms."""

from __future__ import annotations

from dataclasses import dataclass

from engines.timing_fit import DUB_SLOT_TOLERANCE_MS


@dataclass
class SlotFitResult:
    ok: bool
    score: float
    predicted_ms: int
    slot_ms: int
    delta_ms: int
    overflow_ms: int
    underflow_ms: int


def validate_slot_fit(
    predicted_ms: int,
    slot_ms: int,
    *,
    tolerance_ms: int | None = None,
) -> SlotFitResult:
    tol = tolerance_ms if tolerance_ms is not None else DUB_SLOT_TOLERANCE_MS
    slot = max(1, int(slot_ms))
    pred = max(0, int(predicted_ms))
    delta = pred - slot
    overflow = max(0, delta - tol)
    underflow = max(0, -delta - tol) if pred < int(slot * 0.82) else 0

    if overflow <= 0 and underflow <= 0:
        score = 1.0
    elif overflow > 0:
        ratio = overflow / slot
        score = max(0.0, 1.0 - min(1.0, ratio * 2.5))
    else:
        ratio = underflow / slot
        score = max(0.0, 1.0 - min(0.85, ratio * 1.8))

    ok = score >= 0.75 and overflow <= tol
    return SlotFitResult(
        ok=ok,
        score=round(score, 4),
        predicted_ms=pred,
        slot_ms=slot,
        delta_ms=delta,
        overflow_ms=overflow,
        underflow_ms=underflow,
    )


def slot_fit_score(predicted_ms: int, slot_ms: int, *, tolerance_ms: int | None = None) -> float:
    """Return slot fit score 0-1 for predicted vs slot duration."""
    return validate_slot_fit(predicted_ms, slot_ms, tolerance_ms=tolerance_ms).score


def slot_fit_score(
    predicted_ms: int,
    slot_ms: int,
    *,
    tolerance_ms: int | None = None,
) -> float:
    """0-1 score for predicted duration vs slot."""
    return validate_slot_fit(
        predicted_ms, slot_ms, tolerance_ms=tolerance_ms
    ).score
