"""Emotion preservation — reuse semantic emotion_validator."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.semantic_agent.validators.emotion_validator import validate_emotion


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_emotion(source: str, translated: str, candidate: str) -> CheckResult:
    result = validate_emotion(source, translated, candidate)
    return CheckResult(
        ok=result.ok,
        score=result.score,
        failure_type=None if result.ok else "emotion",
        issues=result.issues,
    )
