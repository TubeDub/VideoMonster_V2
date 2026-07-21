"""TQE data model — single QualityReport shape for every Reviewer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ReviewStatus(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    WARN = "WARN"
    SKIP = "SKIP"


class RetryStrategyName(str, Enum):
    NONE = "none"
    MEANING_PRESERVATION = "Meaning Preservation Strategy"
    ENTITY_CORRECTION = "Entity Correction Strategy"
    COMPLETION = "Completion Strategy"
    GRAMMAR = "Grammar Strategy"
    TIMING = "Timing Strategy"
    NARRATIVE = "Narrative Strategy"


@dataclass
class ConfidenceMetrics:
    entity_preservation: float = 1.0
    meaning_coverage: float = 1.0
    grammar_integrity: float = 1.0
    sentence_completeness: float = 1.0
    narrative_integrity: float = 1.0
    timing_fitness: float = 1.0

    def overall(self, *, weights: dict[str, float] | None = None) -> float:
        w = weights or {
            "entity_preservation": 0.20,
            "meaning_coverage": 0.25,
            "grammar_integrity": 0.15,
            "sentence_completeness": 0.15,
            "narrative_integrity": 0.15,
            "timing_fitness": 0.10,
        }
        total_w = 0.0
        acc = 0.0
        for key, weight in w.items():
            acc += float(getattr(self, key, 1.0)) * float(weight)
            total_w += float(weight)
        return round(acc / max(total_w, 1e-9), 4)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["overall_confidence"] = self.overall()
        return d


@dataclass
class QualityReport:
    reviewer_name: str
    status: ReviewStatus = ReviewStatus.PASS
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    explanation: str = ""
    retry_strategy: str = RetryStrategyName.NONE.value
    metadata: dict[str, Any] = field(default_factory=dict)
    review_time_ms: float = 0.0
    retry_count: int = 0
    llm_used: bool = False
    fallback_used: bool = False
    confidence_history: list[float] = field(default_factory=list)
    confidence: ConfidenceMetrics | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_name": self.reviewer_name,
            "status": self.status.value if isinstance(self.status, ReviewStatus) else str(self.status),
            "metrics": dict(self.metrics),
            "errors": list(self.errors),
            "explanation": self.explanation,
            "retry_strategy": self.retry_strategy,
            "metadata": dict(self.metadata),
            "review_time_ms": round(float(self.review_time_ms), 2),
            "retry_count": int(self.retry_count),
            "llm_used": bool(self.llm_used),
            "fallback_used": bool(self.fallback_used),
            "confidence_history": list(self.confidence_history),
            "confidence": self.confidence.to_dict() if self.confidence else None,
        }

    @property
    def rejected(self) -> bool:
        return self.status == ReviewStatus.REJECT


@dataclass
class SegmentQualityDecision:
    index: int
    status: ReviewStatus
    overall_confidence: float
    reports: list[QualityReport] = field(default_factory=list)
    explanation: str = ""
    retry_strategy: str = RetryStrategyName.NONE.value
    original: str = ""
    translation: str = ""
    allowed_for_tts: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "status": self.status.value,
            "overall_confidence": self.overall_confidence,
            "explanation": self.explanation,
            "retry_strategy": self.retry_strategy,
            "original": self.original,
            "translation": self.translation,
            "allowed_for_tts": self.allowed_for_tts,
            "reports": [r.to_dict() for r in self.reports],
        }


@dataclass
class TQEBatchResult:
    task_id: str
    decisions: list[SegmentQualityDecision] = field(default_factory=list)
    passed: int = 0
    rejected: int = 0
    overall_confidence: float = 0.0
    gate_passed: bool = False
    blocked_indices: list[int] = field(default_factory=list)
    analytics: dict[str, Any] = field(default_factory=dict)
    explanations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "passed": self.passed,
            "rejected": self.rejected,
            "overall_confidence": self.overall_confidence,
            "gate_passed": self.gate_passed,
            "blocked_indices": list(self.blocked_indices),
            "analytics": dict(self.analytics),
            "explanations": list(self.explanations),
            "decisions": [d.to_dict() for d in self.decisions],
        }
