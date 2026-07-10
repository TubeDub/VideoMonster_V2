"""Quality scoring — Overall, Meaning, Grammar, Timing, Naturalness, Emotion, Voice Readiness, Entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


WEIGHTS = {
    "meaning": 0.20,
    "grammar": 0.15,
    "timing": 0.15,
    "naturalness": 0.12,
    "emotion": 0.08,
    "voice_readiness": 0.12,
    "entity": 0.10,
    "syntax": 0.08,
}


@dataclass
class QualityScores:
    overall: float
    meaning: float
    grammar: float
    timing: float
    naturalness: float
    emotion: float
    voice_readiness: float
    entity: float
    syntax: float = 1.0

    def to_dict(self) -> dict[str, float]:
        return {
            "overall": round(self.overall, 4),
            "meaning": round(self.meaning, 4),
            "grammar": round(self.grammar, 4),
            "timing": round(self.timing, 4),
            "naturalness": round(self.naturalness, 4),
            "emotion": round(self.emotion, 4),
            "voice_readiness": round(self.voice_readiness, 4),
            "entity": round(self.entity, 4),
            "syntax": round(self.syntax, 4),
        }


@dataclass
class SegmentAuditResult:
    index: int
    scores: QualityScores
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    failure_types: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "scores": self.scores.to_dict(),
            "checks": self.checks,
            "failure_types": self.failure_types,
            "reasons": self.reasons,
            "critical": self.critical,
        }


def compute_overall(
    *,
    meaning: float,
    grammar: float,
    timing: float,
    naturalness: float,
    emotion: float,
    voice_readiness: float,
    entity: float,
    syntax: float = 1.0,
) -> float:
    return round(
        WEIGHTS["meaning"] * meaning
        + WEIGHTS["grammar"] * grammar
        + WEIGHTS["timing"] * timing
        + WEIGHTS["naturalness"] * naturalness
        + WEIGHTS["emotion"] * emotion
        + WEIGHTS["voice_readiness"] * voice_readiness
        + WEIGHTS["entity"] * entity
        + WEIGHTS["syntax"] * syntax,
        4,
    )


def aggregate_averages(audits: list[SegmentAuditResult]) -> dict[str, float]:
    if not audits:
        return {k: 0.0 for k in ("overall", "meaning", "grammar", "timing", "naturalness", "emotion", "voice_readiness", "entity", "syntax")}
    keys = ("overall", "meaning", "grammar", "timing", "naturalness", "emotion", "voice_readiness", "entity", "syntax")
    totals = {k: 0.0 for k in keys}
    for audit in audits:
        d = audit.scores.to_dict()
        for k in keys:
            totals[k] += float(d.get(k) or 0)
    n = len(audits)
    return {k: round(totals[k] / n, 4) for k in keys}
