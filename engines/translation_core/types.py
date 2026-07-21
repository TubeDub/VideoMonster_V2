"""Translation Core types — Master Spec Part 3."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


def _uid() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class ScoreCard:
    meaning: float = 0.0
    entity: float = 0.0
    grammar: float = 0.0
    naturalness: float = 0.0
    terminology: float = 0.0
    context: float = 0.0
    style: float = 0.0
    completeness: float = 0.0
    similarity: float = 0.0
    confidence: float = 0.0

    def average(self) -> float:
        vals = [
            self.meaning,
            self.entity,
            self.grammar,
            self.naturalness,
            self.terminology,
            self.context,
            self.style,
            self.completeness,
            self.similarity,
        ]
        return sum(vals) / max(1, len(vals))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["average"] = round(self.average(), 3)
        return d


@dataclass
class TranslationVariant:
    variant_id: str = field(default_factory=_uid)
    label: str = "A"
    text: str = ""
    backend_id: str = ""
    scores: ScoreCard = field(default_factory=ScoreCard)
    rejected: bool = False
    reject_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "label": self.label,
            "text": self.text,
            "backend_id": self.backend_id,
            "scores": self.scores.to_dict(),
            "rejected": self.rejected,
            "reject_reasons": list(self.reject_reasons),
            "warnings": list(self.warnings),
        }


@dataclass
class SentenceTranslationReport:
    sentence_uuid: str
    source_text: str
    selected_variant_id: str = ""
    selected_text: str = ""
    selected_label: str = ""
    selection_reason: str = ""
    variants: list[TranslationVariant] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    locked: bool = False
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentence_uuid": self.sentence_uuid,
            "source_text": self.source_text,
            "selected_variant_id": self.selected_variant_id,
            "selected_text": self.selected_text,
            "selected_label": self.selected_label,
            "selection_reason": self.selection_reason,
            "variants": [v.to_dict() for v in self.variants],
            "warnings": list(self.warnings),
            "locked": self.locked,
            "confidence": self.confidence,
        }


@dataclass
class TranslationCoreResult:
    sentences: list[Any]
    reports: list[SentenceTranslationReport] = field(default_factory=list)
    backend_id: str = ""
    locked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "locked": self.locked,
            "reports": [r.to_dict() for r in self.reports],
            "sentence_count": len(self.sentences),
        }
