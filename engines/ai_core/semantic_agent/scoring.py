"""Meaning/Naturalness/Context/Emotion scores 0-1."""

from __future__ import annotations

import re
from dataclasses import dataclass

from engines.ai_core.semantic_agent.validators.context_validator import (
    ContextValidationResult,
    validate_context,
)
from engines.ai_core.semantic_agent.validators.emotion_validator import (
    EmotionValidationResult,
    validate_emotion,
)
from engines.ai_core.semantic_agent.validators.meaning_validator import (
    MeaningValidationResult,
    validate_meaning,
)

# Weighted selection weights
WEIGHTS = {
    "meaning": 0.40,
    "naturalness": 0.25,
    "context": 0.20,
    "emotion": 0.15,
}


@dataclass
class SegmentScores:
    meaning: float
    naturalness: float
    context: float
    emotion: float
    overall: float

    def to_dict(self) -> dict[str, float]:
        return {
            "meaning": round(self.meaning, 4),
            "naturalness": round(self.naturalness, 4),
            "context": round(self.context, 4),
            "emotion": round(self.emotion, 4),
            "overall": round(self.overall, 4),
        }


@dataclass
class CandidateScore:
    variant: str
    text: str
    scores: SegmentScores
    meaning_detail: MeaningValidationResult
    context_detail: ContextValidationResult
    emotion_detail: EmotionValidationResult
    source: str = "rule"  # rule|llm


def _naturalness_score(raw: str, candidate: str, tgt_lang: str) -> float:
    """Heuristic naturalness: fewer MT artifacts, reasonable length."""
    cand = str(candidate or "").strip()
    raw_s = str(raw or "").strip()
    if not cand:
        return 0.0

    score = 0.55
    # Penalize literal artifacts
    bad_patterns = [
        r"\b(?:является|осуществляет|данный|в настоящее время)\b",
        r"\b(?:здійснює|даний|в даний час)\b",
        r"\b(?:он|она|они)\s+ест\b",
        r"\b(?:він|вона|вони)\s+є\b",
        r"\b(?:который|який)\s+(?:который|який)\b",
    ]
    for pat in bad_patterns:
        if re.search(pat, cand, re.IGNORECASE):
            score -= 0.08

    # Reward punctuation polish
    if re.search(r"[,.!?;:]", cand):
        score += 0.05

    # Don't reward shortening (semantic agent must not shorten for timing)
    if len(cand) < len(raw_s) * 0.85 and len(raw_s) > 20:
        score -= 0.15
    elif cand != raw_s:
        score += 0.1

    # Slight reward for changed but similar length (natural rewrite)
    len_ratio = len(cand) / max(len(raw_s), 1)
    if 0.9 <= len_ratio <= 1.15:
        score += 0.1

    return round(max(0.0, min(1.0, score)), 4)


def score_candidate(
    source: str,
    translated: str,
    candidate: str,
    *,
    variant: str,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    source_kind: str = "rule",
) -> CandidateScore:
    """Score one candidate across all dimensions."""
    meaning = validate_meaning(source, translated, candidate)
    context = validate_context(source, translated, candidate, prev_context=prev_context)
    emotion = validate_emotion(source, translated, candidate)
    naturalness = _naturalness_score(translated, candidate, tgt_lang)

    overall = round(
        WEIGHTS["meaning"] * meaning.score
        + WEIGHTS["naturalness"] * naturalness
        + WEIGHTS["context"] * context.score
        + WEIGHTS["emotion"] * emotion.score,
        4,
    )
    scores = SegmentScores(
        meaning=meaning.score,
        naturalness=naturalness,
        context=context.score,
        emotion=emotion.score,
        overall=overall,
    )
    return CandidateScore(
        variant=variant,
        text=candidate,
        scores=scores,
        meaning_detail=meaning,
        context_detail=context,
        emotion_detail=emotion,
        source=source_kind,
    )


def aggregate_averages(per_segment: list[SegmentScores]) -> dict[str, float]:
    if not per_segment:
        return {"meaning": 0.0, "naturalness": 0.0, "context": 0.0, "emotion": 0.0, "overall": 0.0}
    n = len(per_segment)
    return {
        "meaning": round(sum(s.meaning for s in per_segment) / n, 4),
        "naturalness": round(sum(s.naturalness for s in per_segment) / n, 4),
        "context": round(sum(s.context for s in per_segment) / n, 4),
        "emotion": round(sum(s.emotion for s in per_segment) / n, 4),
        "overall": round(sum(s.overall for s in per_segment) / n, 4),
    }
