"""Pass 3 — facts, actions, causes preserved."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engines.ai_core.translation_agent.validators.entity_validator import (
    extract_entities,
    validate_entities,
)

_ACTION_VERBS = re.compile(
    r"\b(?:went|came|said|told|asked|made|took|gave|got|left|started|"
    r"stopped|died|born|visited|launched|signed|called|"
    r"пошёл|пошла|пришёл|пришла|сказал|сказала|сделал|сделала|"
    r"взял|взяла|отдал|отдала|умер|родился|посетил|запустил|"
    r"підписав|підписала|пішов|пішла|прийшов|прийшла|сказав|сказала)\b",
    re.IGNORECASE,
)
_CAUSE_MARKERS = re.compile(
    r"\b(?:because|since|therefore|so that|due to|"
    r"потому что|поэтому|из-за|так что|"
    r"тому що|тому|через|бо)\b",
    re.IGNORECASE,
)


@dataclass
class MeaningValidationResult:
    ok: bool
    score: float
    entity_score: float = 1.0
    action_preserved: bool = True
    cause_preserved: bool = True
    missing_entities: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _keyword_overlap(source: str, candidate: str, pattern: re.Pattern) -> tuple[float, bool]:
    src_hits = {m.group(0).lower() for m in pattern.finditer(source or "")}
    if not src_hits:
        return 1.0, True
    cand = (candidate or "").lower()
    preserved = sum(1 for h in src_hits if h in cand)
    ratio = preserved / len(src_hits)
    return ratio, ratio >= 0.5


def validate_meaning(
    source: str,
    translated: str,
    candidate: str,
) -> MeaningValidationResult:
    """Check that key facts, actions, and causal links are preserved."""
    entity_result = validate_entities(source, candidate)
    entity_score = entity_result.confidence

    action_score, action_ok = _keyword_overlap(source, candidate, _ACTION_VERBS)
    cause_score, cause_ok = _keyword_overlap(source, candidate, _CAUSE_MARKERS)

    # Also ensure candidate didn't lose entities from raw translation
    trans_entities = extract_entities(translated)
    cand = str(candidate or "")
    trans_preserved = 0
    for ent in trans_entities:
        if ent in cand or ent.lower() in cand.lower():
            trans_preserved += 1
    trans_ratio = trans_preserved / max(len(trans_entities), 1) if trans_entities else 1.0

    score = round(
        0.45 * entity_score + 0.20 * action_score + 0.15 * cause_score + 0.20 * trans_ratio,
        4,
    )
    issues: list[str] = []
    if entity_result.missing:
        issues.append(f"missing_entities:{entity_result.missing[:3]}")
    if not action_ok:
        issues.append("actions_weakened")
    if not cause_ok:
        issues.append("causes_weakened")

    ok = score >= 0.75 and entity_score >= 0.6
    return MeaningValidationResult(
        ok=ok,
        score=score,
        entity_score=entity_score,
        action_preserved=action_ok,
        cause_preserved=cause_ok,
        missing_entities=entity_result.missing,
        issues=issues,
    )
