"""ЭТАП 7 — Time Equivalence Check.

For every adapted sentence, compare the *original* source duration to
the *adapted* (predicted) TTS duration. If the delta exceeds the
configured tolerance, mark the sentence as ``needs_readaptation`` and
return the offending list to Meaning Fit for exactly one extra pass.

Design constraints from the TZ:

- Never loop indefinitely. Hard cap of one extra iteration per call to
  :func:`evaluate_and_mark`.
- Never silently repair the input; the marker is advisory. Callers
  decide whether to re-run Meaning Fit or reject.
- Never mutate the source ``start_ms`` / ``end_ms`` — the check is
  read-only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence

logger = logging.getLogger("tubedub.semantic_v3.time_equivalence")

# Default tolerance: an adapted TTS duration may deviate from the source
# speech window by at most 15% before Meaning Fit is asked to re-run.
_DEFAULT_TOLERANCE_PCT = 15.0


@dataclass
class TimeEquivalenceResult:
    """One row in the time-equivalence report."""

    sentence_uuid: str
    original_duration_ms: int
    adapted_duration_ms: int
    delta_ms: int
    delta_pct: float
    needs_readaptation: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentence_uuid": self.sentence_uuid,
            "original_duration_ms": self.original_duration_ms,
            "adapted_duration_ms": self.adapted_duration_ms,
            "delta_ms": self.delta_ms,
            "delta_pct": round(self.delta_pct, 3),
            "needs_readaptation": self.needs_readaptation,
            "reason": self.reason,
        }


@dataclass
class TimeEquivalenceReport:
    tolerance_pct: float
    rows: list[TimeEquivalenceResult] = field(default_factory=list)

    @property
    def flagged(self) -> list[TimeEquivalenceResult]:
        return [r for r in self.rows if r.needs_readaptation]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tolerance_pct": self.tolerance_pct,
            "flagged_count": len(self.flagged),
            "rows": [r.to_dict() for r in self.rows],
        }


def evaluate_and_mark(
    sentences: list[SemanticSentence],
    *,
    tolerance_pct: float = _DEFAULT_TOLERANCE_PCT,
) -> TimeEquivalenceReport:
    """ЭТАП 7 — compute original→adapted delta and mark outliers.

    ``sentences`` must have run through the duration predictor (so
    ``predicted_tts_ms`` is populated). The check compares that against
    the source ``slot_ms`` — because the source cadence *is* the ground
    truth for lip-synced dubbing.

    Sentences with delta > tolerance are stamped with the attribute
    ``needs_readaptation = True``; the caller is expected to route
    exactly that subset back to Meaning Fit once. To make double
    iteration impossible, this function also stamps
    ``time_equivalence_pass = N`` on each sentence so callers can
    guard the loop with ``pass < 2``.
    """
    report = TimeEquivalenceReport(tolerance_pct=tolerance_pct)
    for sent in sentences:
        original = int(sent.slot_ms)
        adapted = int(sent.predicted_tts_ms or 0)
        delta = adapted - original
        delta_pct = (abs(delta) / original * 100.0) if original > 0 else 0.0

        current_pass = int(getattr(sent, "time_equivalence_pass", 0) or 0)
        exceeds_tolerance = original > 0 and delta_pct > tolerance_pct

        needs = exceeds_tolerance and current_pass < 1
        reason = ""
        if exceeds_tolerance and current_pass >= 1:
            reason = "exhausted_readaptation_budget"
        elif exceeds_tolerance:
            reason = "delta_exceeds_tolerance"
        elif original <= 0:
            reason = "no_source_slot"
        else:
            reason = "within_tolerance"

        setattr(sent, "needs_readaptation", bool(needs))
        setattr(sent, "time_equivalence_pass", current_pass)
        setattr(
            sent,
            "time_equivalence",
            {
                "original_duration_ms": original,
                "adapted_duration_ms": adapted,
                "delta_ms": delta,
                "delta_pct": round(delta_pct, 3),
                "tolerance_pct": tolerance_pct,
                "pass": current_pass,
                "reason": reason,
            },
        )
        report.rows.append(
            TimeEquivalenceResult(
                sentence_uuid=sent.sentence_uuid,
                original_duration_ms=original,
                adapted_duration_ms=adapted,
                delta_ms=delta,
                delta_pct=delta_pct,
                needs_readaptation=bool(needs),
                reason=reason,
            )
        )

    logger.info(
        "time_equivalence: sentences=%d flagged=%d tol=%.1f%%",
        len(sentences),
        len(report.flagged),
        tolerance_pct,
    )
    return report


def mark_readaptation_pass(sentences: list[SemanticSentence]) -> None:
    """Bump the pass counter *after* Meaning Fit has re-run once.

    This is what makes the loop cap at exactly one extra iteration:
    the second call to :func:`evaluate_and_mark` will see
    ``time_equivalence_pass >= 1`` and refuse to mark any more work.
    """
    for s in sentences:
        current = int(getattr(s, "time_equivalence_pass", 0) or 0)
        setattr(s, "time_equivalence_pass", current + 1)
