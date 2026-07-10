"""Grammar quality — reuse grammar_agent validators."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.grammar_agent.validators.grammar_validator import validate_grammar


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_grammar(text: str, *, tgt_lang: str = "ru") -> CheckResult:
    result = validate_grammar(text, tgt_lang=tgt_lang)
    return CheckResult(
        ok=result.ok,
        score=result.score,
        failure_type=None if result.ok else "grammar",
        issues=result.issues,
    )
