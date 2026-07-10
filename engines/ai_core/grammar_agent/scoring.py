"""Grammar/Syntax/Style/Naturalness/Pronunciation/Readability scores 0-1."""

from __future__ import annotations

from dataclasses import dataclass

from engines.ai_core.grammar_agent.validators.grammar_validator import validate_grammar
from engines.ai_core.grammar_agent.validators.meaning_preservation import (
    MeaningPreservationResult,
    validate_meaning_preservation,
)
from engines.ai_core.grammar_agent.validators.natural_speech_validator import (
    validate_natural_speech,
)
from engines.ai_core.grammar_agent.validators.sentence_integrity import (
    validate_sentence_integrity,
)
from engines.ai_core.grammar_agent.validators.style_validator import validate_style
from engines.ai_core.grammar_agent.validators.syntax_validator import validate_syntax

WEIGHTS = {
    "grammar": 0.20,
    "syntax": 0.15,
    "style": 0.10,
    "naturalness": 0.15,
    "pronunciation": 0.10,
    "readability": 0.10,
    "meaning": 0.20,
}

LENGTH_TOLERANCE = 0.15


@dataclass
class SegmentScores:
    grammar: float
    syntax: float
    style: float
    naturalness: float
    pronunciation: float
    readability: float
    meaning: float
    overall: float

    def to_dict(self) -> dict[str, float]:
        return {
            "grammar": round(self.grammar, 4),
            "syntax": round(self.syntax, 4),
            "style": round(self.style, 4),
            "naturalness": round(self.naturalness, 4),
            "pronunciation": round(self.pronunciation, 4),
            "readability": round(self.readability, 4),
            "meaning": round(self.meaning, 4),
            "overall": round(self.overall, 4),
        }


@dataclass
class CandidateScore:
    variant: str
    text: str
    scores: SegmentScores
    meaning_detail: MeaningPreservationResult
    length_ratio: float
    source: str = "rule"


def length_ratio(reference: str, candidate: str) -> float:
    ref_len = max(len(str(reference or "").strip()), 1)
    return len(str(candidate or "").strip()) / ref_len


def length_within_tolerance(reference: str, candidate: str, tolerance: float = LENGTH_TOLERANCE) -> bool:
    ratio = length_ratio(reference, candidate)
    return (1.0 - tolerance) <= ratio <= (1.0 + tolerance)


def _readability_score(reference: str, candidate: str) -> float:
    cand = str(candidate or "").strip()
    if not cand:
        return 0.0
    score = 0.7
    words = cand.split()
    if 3 <= len(words) <= 30:
        score += 0.1
    avg_len = sum(len(w) for w in words) / max(len(words), 1)
    if avg_len <= 12:
        score += 0.1
    ratio = length_ratio(reference, candidate)
    if 0.9 <= ratio <= 1.1:
        score += 0.1
    return round(min(1.0, score), 4)


def _pronunciation_score(text: str) -> float:
    cand = str(text or "")
    if not cand:
        return 0.0
    score = 1.0
    import re

    if re.search(
        r"([бвгджзклмнпрстфхцчшщbcdfghjklmnpqrstvwxyz])\1{2,}",
        cand,
        re.I,
    ):
        score -= 0.25
    if "\u200b" in cand:
        score += 0.05
    return round(max(0.0, min(1.0, score)), 4)


def score_candidate(
    source: str,
    reference: str,
    candidate: str,
    *,
    variant: str,
    tgt_lang: str = "ru",
    source_kind: str = "rule",
) -> CandidateScore | None:
    """Score one candidate; returns None if length guard fails."""
    if not length_within_tolerance(reference, candidate):
        return None

    grammar = validate_grammar(candidate, tgt_lang=tgt_lang)
    syntax = validate_syntax(candidate)
    style = validate_style(reference, candidate)
    naturalness = validate_natural_speech(candidate, tgt_lang=tgt_lang)
    integrity = validate_sentence_integrity(reference, candidate)
    meaning = validate_meaning_preservation(source, reference, candidate)
    pronunciation = _pronunciation_score(candidate)
    readability = _readability_score(reference, candidate)

    if not integrity.ok:
        return None

    overall = round(
        WEIGHTS["grammar"] * grammar.score
        + WEIGHTS["syntax"] * syntax.score
        + WEIGHTS["style"] * style.score
        + WEIGHTS["naturalness"] * naturalness.score
        + WEIGHTS["pronunciation"] * pronunciation
        + WEIGHTS["readability"] * readability
        + WEIGHTS["meaning"] * meaning.score,
        4,
    )
    scores = SegmentScores(
        grammar=grammar.score,
        syntax=syntax.score,
        style=style.score,
        naturalness=naturalness.score,
        pronunciation=pronunciation,
        readability=readability,
        meaning=meaning.score,
        overall=overall,
    )
    return CandidateScore(
        variant=variant,
        text=candidate,
        scores=scores,
        meaning_detail=meaning,
        length_ratio=length_ratio(reference, candidate),
        source=source_kind,
    )


def aggregate_averages(per_segment: list[SegmentScores]) -> dict[str, float]:
    if not per_segment:
        return {
            "grammar": 0.0,
            "syntax": 0.0,
            "style": 0.0,
            "naturalness": 0.0,
            "pronunciation": 0.0,
            "readability": 0.0,
            "meaning": 0.0,
            "overall": 0.0,
        }
    n = len(per_segment)
    keys = ("grammar", "syntax", "style", "naturalness", "pronunciation", "readability", "meaning", "overall")
    return {k: round(sum(getattr(s, k) for s in per_segment) / n, 4) for k in keys}
