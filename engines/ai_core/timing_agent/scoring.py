"""Slot Fit Score and Timing Score 0-1."""

from __future__ import annotations

from dataclasses import dataclass

from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
from engines.ai_core.timing_agent.validators.meaning_check import validate_meaning_preserved
from engines.ai_core.timing_agent.validators.naturalness_check import validate_naturalness
from engines.ai_core.timing_agent.validators.sentence_integrity import validate_sentence_integrity
from engines.ai_core.timing_agent.validators.slot_fit_validator import slot_fit_score

WEIGHTS = {
    "slot_fit": 0.45,
    "meaning": 0.30,
    "naturalness": 0.15,
    "integrity": 0.10,
}


@dataclass
class TimingScores:
    slot_fit: float
    meaning: float
    naturalness: float
    integrity: float
    timing: float
    overall: float

    def to_dict(self) -> dict[str, float]:
        return {
            "slot_fit": round(self.slot_fit, 4),
            "meaning": round(self.meaning, 4),
            "naturalness": round(self.naturalness, 4),
            "integrity": round(self.integrity, 4),
            "timing": round(self.timing, 4),
            "overall": round(self.overall, 4),
        }


@dataclass
class CandidateTimingScore:
    variant: str
    text: str
    predicted_ms: int
    scores: TimingScores
    source: str = "rule"


ScoredCandidate = CandidateTimingScore


def timing_score(predicted_ms: int, slot_ms: int) -> float:
    """Alias for slot fit component."""
    return slot_fit_score(predicted_ms, slot_ms)


def score_candidate(
    candidate: str,
    *,
    source: str,
    semantic_text: str,
    slot_ms: int,
    tgt_lang: str = "ru",
    variant: str = "A",
    source_kind: str = "rule",
    app_dir=None,
) -> CandidateTimingScore:
    predicted_ms = predict_duration_ms(candidate, tgt_lang, app_dir=app_dir)
    fit = slot_fit_score(predicted_ms, slot_ms)
    meaning = validate_meaning_preserved(source, semantic_text, candidate)
    natural = validate_naturalness(semantic_text, candidate)
    integrity = validate_sentence_integrity(semantic_text, candidate)

    overall = round(
        WEIGHTS["slot_fit"] * fit
        + WEIGHTS["meaning"] * meaning.score
        + WEIGHTS["naturalness"] * natural.score
        + WEIGHTS["integrity"] * integrity.score,
        4,
    )
    scores = TimingScores(
        slot_fit=fit,
        meaning=meaning.score,
        naturalness=natural.score,
        integrity=integrity.score,
        timing=fit,
        overall=overall,
    )
    return CandidateTimingScore(
        variant=variant,
        text=candidate,
        predicted_ms=predicted_ms,
        scores=scores,
        source=source_kind if variant != "LLM" else "llm",
    )


def aggregate_averages(per_segment: list[TimingScores]) -> dict[str, float]:
    if not per_segment:
        return {
            "slot_fit": 0.0,
            "meaning": 0.0,
            "naturalness": 0.0,
            "integrity": 0.0,
            "timing": 0.0,
            "overall": 0.0,
        }
    n = len(per_segment)
    return {
        "slot_fit": round(sum(s.slot_fit for s in per_segment) / n, 4),
        "meaning": round(sum(s.meaning for s in per_segment) / n, 4),
        "naturalness": round(sum(s.naturalness for s in per_segment) / n, 4),
        "integrity": round(sum(s.integrity for s in per_segment) / n, 4),
        "timing": round(sum(s.timing for s in per_segment) / n, 4),
        "overall": round(sum(s.overall for s in per_segment) / n, 4),
    }
