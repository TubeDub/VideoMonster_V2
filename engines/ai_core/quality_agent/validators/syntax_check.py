"""Syntax validation — reuse grammar_agent syntax_validator."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.grammar_agent.validators.syntax_validator import validate_syntax


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_syntax(text: str) -> CheckResult:
    result = validate_syntax(text)
    return CheckResult(
        ok=result.ok,
        score=result.score,
        failure_type=None if result.ok else "syntax",
        issues=result.issues,
    )
