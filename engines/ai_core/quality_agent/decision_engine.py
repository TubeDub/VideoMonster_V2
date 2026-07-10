"""Decision engine — ACCEPT|RETRY|FALLBACK|WARNING|FAIL per segment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from engines.ai_core.quality_agent.scoring import SegmentAuditResult

Decision = Literal["ACCEPT", "RETRY", "FALLBACK", "WARNING", "FAIL"]

OVERALL_ACCEPT_THRESHOLD = 0.75
OVERALL_WARNING_THRESHOLD = 0.55

# Priority order for routing retries
RETRY_PRIORITY = (
    "entity",
    "terminology",
    "language",
    "meaning",
    "timing",
    "slot_fit",
    "grammar",
    "syntax",
    "natural_speech",
    "sentence_integrity",
    "voice_readiness",
)


@dataclass
class SegmentDecision:
    decision: Decision
    failure_type: str | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "failure_type": self.failure_type,
            "reasons": self.reasons,
        }


def pick_primary_failure(failure_types: list[str]) -> str | None:
    for ft in RETRY_PRIORITY:
        if ft in failure_types:
            return ft
    return failure_types[0] if failure_types else None


def decide(
    audit: SegmentAuditResult,
    *,
    retry_count: int = 0,
    max_retries: int = 3,
    debug_mode: bool = False,
) -> SegmentDecision:
    """
    Map audit result to pipeline decision.

    Critical FAIL only: empty segment, corrupted text, NULL.
    Pipeline continues in debug mode on WARNING/FALLBACK.
    """
    if audit.critical:
        if debug_mode:
            return SegmentDecision(
                decision="WARNING",
                failure_type="critical",
                reasons=audit.reasons[:5] + ["quality_fail_downgraded_to_warning"],
            )
        return SegmentDecision(
            decision="FAIL",
            failure_type="critical",
            reasons=audit.reasons,
        )

    if not audit.failure_types and audit.scores.overall >= OVERALL_ACCEPT_THRESHOLD:
        return SegmentDecision(decision="ACCEPT", reasons=[])

    primary = pick_primary_failure(audit.failure_types)

    if audit.failure_types and retry_count < max_retries:
        return SegmentDecision(
            decision="RETRY",
            failure_type=primary,
            reasons=audit.reasons[:5],
        )

    if retry_count >= max_retries and audit.failure_types:
        if audit.scores.overall >= OVERALL_WARNING_THRESHOLD:
            return SegmentDecision(
                decision="FALLBACK",
                failure_type=primary,
                reasons=audit.reasons[:5] + ["max_retries_exhausted"],
            )
        if debug_mode:
            return SegmentDecision(
                decision="WARNING",
                failure_type=primary,
                reasons=audit.reasons[:5] + ["debug_mode_continue"],
            )
        return SegmentDecision(
            decision="FALLBACK",
            failure_type=primary,
            reasons=audit.reasons[:5] + ["max_retries_exhausted"],
        )

    if audit.scores.overall >= OVERALL_WARNING_THRESHOLD:
        return SegmentDecision(
            decision="WARNING" if debug_mode or audit.failure_types else "ACCEPT",
            failure_type=primary,
            reasons=audit.reasons[:5],
        )

    if debug_mode:
        return SegmentDecision(
            decision="WARNING",
            failure_type=primary,
            reasons=audit.reasons[:5],
        )

    return SegmentDecision(
        decision="FALLBACK",
        failure_type=primary,
        reasons=audit.reasons[:5],
    )
