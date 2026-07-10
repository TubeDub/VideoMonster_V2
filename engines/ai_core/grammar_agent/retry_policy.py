"""Retry policy — meaning/length failures, max 3 attempts."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.grammar_agent.rule_engine import apply_grammar_pass
from engines.ai_core.grammar_agent.scoring import length_within_tolerance
from engines.ai_core.grammar_agent.validators.meaning_preservation import (
    validate_meaning_preservation,
)

MEANING_THRESHOLD = 0.75
MAX_RETRIES = 3


@dataclass
class RetryResult:
    text: str
    meaning_score: float
    attempts: int
    used_fallback: bool
    decision_log: list[str] = field(default_factory=list)


def apply_retry_policy(
    source: str,
    timing_text: str,
    candidate: str,
    meaning_score: float,
    *,
    tgt_lang: str = "ru",
    max_retries: int = MAX_RETRIES,
) -> RetryResult:
    """
    Retry when meaning score is below threshold or length guard fails.
    After max retries: fallback to timing_text.
    """
    decision_log: list[str] = []
    current = candidate
    score = meaning_score
    attempts = 1
    reference = str(timing_text or "").strip()

    while (
        (score < MEANING_THRESHOLD or not length_within_tolerance(reference, current))
        and attempts < max_retries
    ):
        decision_log.append(
            f"retry attempt={attempts + 1} meaning={score:.3f} "
            f"len_ok={length_within_tolerance(reference, current)}"
        )
        current = apply_grammar_pass(reference, tgt_lang=tgt_lang)
        score = validate_meaning_preservation(source, reference, current).score
        attempts += 1

    used_fallback = False
    if score < MEANING_THRESHOLD or not length_within_tolerance(reference, current):
        decision_log.append(f"fallback_to_timing_text meaning={score:.3f}")
        current = reference
        used_fallback = True

    return RetryResult(
        text=current,
        meaning_score=score,
        attempts=attempts,
        used_fallback=used_fallback,
        decision_log=decision_log,
    )
