"""Facts/entities still present after timing adaptation."""

from __future__ import annotations

from engines.ai_core.semantic_agent.validators.meaning_validator import (
    MeaningValidationResult,
    validate_meaning,
)


def validate_meaning_preserved(
    source: str,
    semantic_text: str,
    candidate: str,
) -> MeaningValidationResult:
    """Reuse semantic meaning validator — timing must not drop facts."""
    return validate_meaning(source, semantic_text, candidate)
