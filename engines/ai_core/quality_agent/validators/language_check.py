"""Language validation — detect garbage and wrong-script output."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.translation_agent.validators.language_validator import (
    validate_language,
)


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    reasons: list[str] = field(default_factory=list)


def check_language(
    source: str,
    candidate: str,
    *,
    source_lang: str = "en",
    target_lang: str = "ru",
    threshold: float = 0.75,
) -> CheckResult:
    result = validate_language(
        source,
        candidate,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    return CheckResult(
        ok=result.ok and result.confidence >= threshold,
        score=result.confidence,
        failure_type=None if result.ok else "language",
        reasons=list(result.issues),
    )
