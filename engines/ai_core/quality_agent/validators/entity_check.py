"""Entity preservation — reuse translation entity_validator."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.translation_agent.validators.entity_validator import validate_entities


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_entity(source: str, candidate: str) -> CheckResult:
    result = validate_entities(source, candidate)
    issues = [f"missing:{m}" for m in result.missing[:5]]
    return CheckResult(
        ok=result.ok,
        score=result.confidence,
        failure_type=None if result.ok else "entity",
        issues=issues,
    )
