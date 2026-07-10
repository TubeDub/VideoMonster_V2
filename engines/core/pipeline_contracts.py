"""Pipeline dataclass contracts — shared between stages (TZ §4–6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WordTiming:
    text: str
    start_ms: int
    end_ms: int
    confidence: float = 1.0
    source: str = "estimated"

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "confidence": round(self.confidence, 3),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WordTiming:
        return cls(
            text=str(data.get("text") or ""),
            start_ms=int(data.get("start_ms", 0)),
            end_ms=int(data.get("end_ms", 0)),
            confidence=float(data.get("confidence", 1.0)),
            source=str(data.get("source") or "estimated"),
        )


@dataclass
class SegmentTiming:
    index: int
    start_ms: int
    end_ms: int
    text: str = ""
    words: list[WordTiming] = field(default_factory=list)
    emotion: str = "neutral"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
            "emotion": self.emotion,
            "meta": dict(self.meta),
        }


@dataclass
class PipelineContext:
    """Mutable context passed through pipeline stages."""

    task_id: str = ""
    video_path: str = ""
    source_lang: str = ""
    target_lang: str = ""
    segments: list[SegmentTiming] = field(default_factory=list)
    word_maps: list[dict[str, Any]] = field(default_factory=list)
    emotions: list[dict[str, Any]] = field(default_factory=list)
    director_report: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, bool] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "video_path": self.video_path,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "segments": [s.to_dict() for s in self.segments],
            "word_maps": list(self.word_maps),
            "emotions": list(self.emotions),
            "director_report": dict(self.director_report),
            "flags": dict(self.flags),
            "meta": dict(self.meta),
        }
