"""Sentence integrity — reuse grammar_agent sentence_integrity."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.grammar_agent.validators.sentence_integrity import (
    validate_sentence_integrity,
)


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_sentence_integrity(original: str, candidate: str) -> CheckResult:
    result = validate_sentence_integrity(original, candidate)
    return CheckResult(
        ok=result.ok,
        score=result.score,
        failure_type=None if result.ok else "sentence_integrity",
        issues=result.issues,
    )
