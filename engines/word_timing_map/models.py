"""Data models for Word Timing Map."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WordToken:
    text: str
    start_ms: int
    end_ms: int
    confidence: float = 1.0

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }
        if self.confidence < 1.0:
            d["confidence"] = round(self.confidence, 3)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WordToken:
        return cls(
            text=str(data.get("text") or ""),
            start_ms=int(data.get("start_ms", 0)),
            end_ms=int(data.get("end_ms", 0)),
            confidence=float(data.get("confidence", 1.0)),
        )


@dataclass
class PauseGap:
    after_word_index: int
    duration_ms: int
    type: str = "natural"

    def to_dict(self) -> dict[str, Any]:
        return {
            "after_word_index": self.after_word_index,
            "duration_ms": self.duration_ms,
            "type": self.type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PauseGap:
        return cls(
            after_word_index=int(data.get("after_word_index", 0)),
            duration_ms=int(data.get("duration_ms", 0)),
            type=str(data.get("type") or "natural"),
        )


@dataclass
class SegmentWordMap:
    segment_index: int
    segment_start_ms: int
    segment_end_ms: int
    words: list[WordToken] = field(default_factory=list)
    pauses_ms: list[PauseGap] = field(default_factory=list)
    timing_source: str = "estimated"  # "real" | "estimated"

    @property
    def speech_ms(self) -> int:
        if not self.words:
            return 0
        return max(w.end_ms for w in self.words) - min(w.start_ms for w in self.words)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_index": self.segment_index,
            "segment_start_ms": self.segment_start_ms,
            "segment_end_ms": self.segment_end_ms,
            "timing_source": self.timing_source,
            "words": [w.to_dict() for w in self.words],
            "pauses_ms": [p.to_dict() for p in self.pauses_ms],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SegmentWordMap:
        return cls(
            segment_index=int(data.get("segment_index", 0)),
            segment_start_ms=int(data.get("segment_start_ms", 0)),
            segment_end_ms=int(data.get("segment_end_ms", 0)),
            words=[WordToken.from_dict(w) for w in (data.get("words") or [])],
            pauses_ms=[PauseGap.from_dict(p) for p in (data.get("pauses_ms") or [])],
            timing_source=str(data.get("timing_source") or "estimated"),
        )


@dataclass
class SemanticUnit:
    """Aligned source↔target unit (Phase 2+) — Meaning Unit."""

    source_indices: list[int]
    source_text: str
    target_indices: list[int]
    target_text: str
    start_ms: int
    end_ms: int
    budget_ms: int = 0
    est_ms: int = 0
    overflow_ms: int = 0
    mutable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_indices": self.source_indices,
            "source_text": self.source_text,
            "target_indices": self.target_indices,
            "target_text": self.target_text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "budget_ms": self.budget_ms,
            "est_ms": self.est_ms,
            "overflow_ms": self.overflow_ms,
            "mutable": self.mutable,
        }


@dataclass
class AlignedSegmentMap:
    """Output of Alignment Engine (Phase 2+)."""

    segment_index: int
    source_words: list[WordToken]
    target_text: str
    units: list[SemanticUnit] = field(default_factory=list)
    timing_source: str = "estimated"
    total_budget_ms: int = 0
    total_est_ms: int = 0
    optimization_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_index": self.segment_index,
            "target_text": self.target_text,
            "timing_source": self.timing_source,
            "source_words": [w.to_dict() for w in self.source_words],
            "units": [u.to_dict() for u in self.units],
            "total_budget_ms": self.total_budget_ms,
            "total_est_ms": self.total_est_ms,
            "optimization_required": self.optimization_required,
        }
