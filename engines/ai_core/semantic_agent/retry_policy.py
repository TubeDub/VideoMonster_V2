"""Retry policy — retry if Meaning Score < 0.75, max 3 attempts."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.semantic_agent.rule_engine import rule_rewrite

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
    translated: str,
    candidate: str,
    meaning_score: float,
    *,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    max_retries: int = MAX_RETRIES,
) -> RetryResult:
    """
    Retry rewrite when meaning score is below threshold.
    After max retries: rule rewrite, then fallback to translated_text.
    """
    decision_log: list[str] = []
    current = candidate
    score = meaning_score
    attempts = 1

    while score < MEANING_THRESHOLD and attempts < max_retries:
        decision_log.append(f"retry attempt={attempts + 1} meaning={score:.3f}")
        current = rule_rewrite(
            translated,
            source=source,
            tgt_lang=tgt_lang,
            prev_context=prev_context,
        )
        from engines.ai_core.semantic_agent.validators.meaning_validator import (
            validate_meaning,
        )

        score = validate_meaning(source, translated, current).score
        attempts += 1

    used_fallback = False
    if score < MEANING_THRESHOLD:
        # v4: never revert to raw machine translation — keep the best adapted candidate.
        decision_log.append(f"keep_candidate meaning={score:.3f}")
        if not str(current or "").strip():
            current = candidate
        used_fallback = bool(str(current or "").strip() != str(candidate or "").strip())

    return RetryResult(
        text=current,
        meaning_score=score,
        attempts=attempts,
        used_fallback=used_fallback,
        decision_log=decision_log,
    )
