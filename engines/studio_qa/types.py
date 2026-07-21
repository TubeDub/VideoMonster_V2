"""Studio QA view models — Pipeline / Timeline / Review / Decision Graph."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


PIPELINE_STAGES: tuple[str, ...] = (
    "Recognition",
    "Sentence",
    "Translation",
    "Validation",
    "Semantic Lock",
    "Planning",
    "Speech",
    "Scheduler",
    "Alignment",
    "Merge",
    "Export",
)


@dataclass
class ReplicaStudioObject:
    """P501 — each replica as a Pipeline object in Studio."""

    segment_id: str
    sentence_uuid: str = ""
    speech_uuid: str = ""
    state: str = ""
    owner: str = ""
    uuid: str = ""
    start_ms: int = 0
    end_ms: int = 0
    status: str = "ok"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    text_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewScores:
    """P504 — Review Panel scores."""

    meaning_score: float = 100.0
    translation_confidence: float = 1.0
    timing_score: float = 100.0
    lipsync_score: float = 100.0
    entity_score: float = 100.0
    speech_score: float = 100.0
    overall_score: float = 100.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StudioQABundle:
    """Unified Studio 2.0 + diagnostics payload."""

    version: str = "6.0"
    pipeline_view: dict[str, Any] = field(default_factory=dict)
    timeline_view: dict[str, Any] = field(default_factory=dict)
    replicas: list[ReplicaStudioObject] = field(default_factory=list)
    review_panel: list[dict[str, Any]] = field(default_factory=list)
    decision_graph_view: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pipeline_view": dict(self.pipeline_view),
            "timeline_view": dict(self.timeline_view),
            "replicas": [r.to_dict() for r in self.replicas],
            "review_panel": list(self.review_panel),
            "decision_graph_view": dict(self.decision_graph_view),
            "metrics": dict(self.metrics),
            "health": dict(self.health),
            "acceptance": dict(self.acceptance),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }
