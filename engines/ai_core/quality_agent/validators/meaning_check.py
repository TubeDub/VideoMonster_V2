"""Meaning preservation — reuse semantic validators."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.semantic_agent.validators.meaning_validator import validate_meaning


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_meaning(
    source: str,
    translated: str,
    candidate: str,
) -> CheckResult:
    result = validate_meaning(source, translated, candidate)
    return CheckResult(
        ok=result.ok,
        score=result.score,
        failure_type=None if result.ok else "meaning",
        issues=result.issues,
    )
