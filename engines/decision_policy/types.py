"""Decision Policy types — strategies, scores, graph (read-only decisions)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


def _uid() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class StrategyCandidate:
    strategy_id: str = field(default_factory=_uid)
    label: str = "A"
    steps: list[str] = field(default_factory=list)
    rejected: bool = False
    reject_reasons: list[str] = field(default_factory=list)
    cost: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    decision_score: float = 0.0
    explanation: str = ""
    expected_fit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionRecord:
    sentence_uuid: str
    problem: str
    profile: str
    candidates: list[StrategyCandidate] = field(default_factory=list)
    accepted: StrategyCandidate | None = None
    rejected: list[StrategyCandidate] = field(default_factory=list)
    reason: str = ""
    rollback_path: list[str] = field(default_factory=list)
    confidences: dict[str, float] = field(default_factory=dict)
    cached: bool = False
    safe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentence_uuid": self.sentence_uuid,
            "problem": self.problem,
            "profile": self.profile,
            "candidates": [c.to_dict() for c in self.candidates],
            "accepted": self.accepted.to_dict() if self.accepted else None,
            "rejected": [c.to_dict() for c in self.rejected],
            "reason": self.reason,
            "rollback_path": list(self.rollback_path),
            "confidences": dict(self.confidences),
            "cached": self.cached,
            "safe": self.safe,
        }


@dataclass
class DecisionGraph:
    scene_uuid: str = ""
    profile: str = ""
    records: list[DecisionRecord] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    timeline_plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_uuid": self.scene_uuid,
            "profile": self.profile,
            "records": [r.to_dict() for r in self.records],
            "conflicts": list(self.conflicts),
            "timeline_plan": dict(self.timeline_plan),
        }
