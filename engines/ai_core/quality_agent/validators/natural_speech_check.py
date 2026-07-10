"""Natural speech — reuse grammar_agent natural_speech_validator."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.grammar_agent.validators.natural_speech_validator import (
    validate_natural_speech,
)


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_natural_speech(text: str, *, tgt_lang: str = "ru") -> CheckResult:
    result = validate_natural_speech(text, tgt_lang=tgt_lang)
    return CheckResult(
        ok=result.ok,
        score=result.score,
        failure_type=None if result.ok else "natural_speech",
        issues=result.issues,
    )
