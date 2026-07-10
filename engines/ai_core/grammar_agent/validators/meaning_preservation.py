"""Meaning preservation — reuse entity patterns from translation_agent."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.translation_agent.validators.entity_validator import (
    extract_entities,
    validate_entities,
)


@dataclass
class MeaningPreservationResult:
    ok: bool
    score: float
    entity_score: float = 1.0
    missing_entities: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def validate_meaning_preservation(
    source: str,
    reference: str,
    candidate: str,
) -> MeaningPreservationResult:
    """Ensure facts/entities from timing reference (and shared source facts) are preserved."""
    ref_entity_result = validate_entities(reference, candidate)
    ref_score = ref_entity_result.confidence

    ref_text = str(reference or "")
    cand = str(candidate or "")
    shared_entities = [
        ent
        for ent in extract_entities(source)
        if ent in ref_text or ent.lower() in ref_text.lower()
    ]
    shared_preserved = sum(
        1 for ent in shared_entities if ent in cand or ent.lower() in cand.lower()
    )
    shared_ratio = shared_preserved / max(len(shared_entities), 1) if shared_entities else 1.0

    score = round(0.7 * ref_score + 0.3 * shared_ratio, 4)
    issues: list[str] = []
    if ref_entity_result.missing:
        issues.append(f"missing_reference_entities:{ref_entity_result.missing[:3]}")
    if shared_ratio < 0.9 and shared_entities:
        issues.append("shared_source_entities_lost")

    ok = score >= 0.75 and ref_score >= 0.6
    return MeaningPreservationResult(
        ok=ok,
        score=score,
        entity_score=ref_score,
        missing_entities=ref_entity_result.missing,
        issues=issues,
    )
