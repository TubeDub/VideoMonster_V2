"""Pass 4 — emotion type preserved (reuse emotion_tagger heuristics)."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.emotion_tagger import classify_segment


@dataclass
class EmotionValidationResult:
    ok: bool
    score: float
    source_emotion: str = "neutral"
    candidate_emotion: str = "neutral"
    issues: list[str] = field(default_factory=list)


def validate_emotion(
    source: str,
    translated: str,
    candidate: str,
) -> EmotionValidationResult:
    """Ensure emotional tone of the translation is preserved in rewrite."""
    src_tag = classify_segment(translated or source, original=source)
    cand_tag = classify_segment(candidate, original=source)

    src_em = src_tag.emotion
    cand_em = cand_tag.emotion

    if src_em == cand_em:
        score = 1.0
    elif src_em == "neutral" or cand_em == "neutral":
        score = 0.75
    elif {src_em, cand_em} <= {"angry", "excited"}:
        score = 0.7
    elif {src_em, cand_em} <= {"sad", "fear"}:
        score = 0.65
    else:
        score = 0.4

    issues: list[str] = []
    if score < 0.6:
        issues.append(f"emotion_shift:{src_em}->{cand_em}")

    return EmotionValidationResult(
        ok=score >= 0.6,
        score=round(score, 4),
        source_emotion=src_em,
        candidate_emotion=cand_em,
        issues=issues,
    )
