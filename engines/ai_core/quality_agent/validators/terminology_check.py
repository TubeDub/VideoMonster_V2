"""Terminology consistency — reuse translation terminology_validator."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.translation_agent.validators.terminology_validator import (
    build_glossary,
    validate_terminology,
)


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_terminology(
    segments: list[dict],
    *,
    glossary: dict[str, int] | None = None,
) -> CheckResult:
    result = validate_terminology(segments, glossary=glossary or build_glossary(segments))
    issues = [f"inconsistent:{t}" for t in result.inconsistent_terms[:5]]
    return CheckResult(
        ok=result.ok,
        score=result.confidence,
        failure_type=None if result.ok else "terminology",
        issues=issues,
    )
