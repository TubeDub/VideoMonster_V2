"""P48 Quality Planner — pre-merge scores; low scores → re-adapt / manual."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from engines.semantic_v3.target_duration_engine import target_ms_for
from engines.semantic_v3.types import SemanticSentence


@dataclass
class QualityPlan:
    sentence_uuid: str
    meaning_score: float
    naturalness_score: float
    speech_score: float
    lipsync_score: float
    duration_score: float
    entity_score: float
    context_score: float
    prosody_score: float
    ok: bool
    action: str  # pass | readapt | manual_review

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _duration_score(sent: SemanticSentence) -> float:
    slot = max(1, target_ms_for(sent))
    pred = int(sent.predicted_tts_ms or slot)
    delta = abs(pred - slot) / slot
    return max(0.0, 100.0 - delta * 200.0)


def _lipsync_score(sent: SemanticSentence) -> float:
    words = sent.words or []
    if not words:
        return 50.0
    with_v = sum(1 for w in words if w.visemes)
    return round(100.0 * with_v / max(1, len(words)), 1)


def _context_score(sent: SemanticSentence) -> float:
    # Isolated sentence (no links) scores lower
    if not sent.context_links:
        return 70.0
    if any(str(r).startswith("ctx:") for r in sent.relations):
        return 95.0
    return 85.0


def plan_quality(
    sentences: list[SemanticSentence],
    *,
    min_score: float = 70.0,
) -> list[QualityPlan]:
    plans: list[QualityPlan] = []
    for s in sentences:
        meaning = float(s.meaning_score or 100.0)
        entity = float(s.entity_score or 100.0)
        duration = _duration_score(s)
        lipsync = _lipsync_score(s)
        context = _context_score(s)
        natural = 100.0 if s.overflow_ms <= 0 else max(40.0, 100.0 - s.overflow_ms / 40.0)
        speech = min(100.0, 100.0 * float(s.speech_rate or 1.0) / 1.05 * 1.05)
        prosody = 90.0 if s.emotion else 80.0
        scores = [meaning, entity, duration, lipsync, context, natural, speech, prosody]
        ok = all(x >= min_score for x in scores)
        action = "pass"
        if not ok:
            action = "manual_review" if min(scores) < 50 else "readapt"
        qp = QualityPlan(
            sentence_uuid=s.sentence_uuid,
            meaning_score=round(meaning, 1),
            naturalness_score=round(natural, 1),
            speech_score=round(speech, 1),
            lipsync_score=round(lipsync, 1),
            duration_score=round(duration, 1),
            entity_score=round(entity, 1),
            context_score=round(context, 1),
            prosody_score=round(prosody, 1),
            ok=ok,
            action=action,
        )
        setattr(s, "quality_plan", qp)
        plans.append(qp)
    return plans
