"""
TubeDub Pipeline Platform — mandatory stage contracts.

No stage may mutate another stage's data directly.
All communication via StageEnvelope in/out only.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StageId(str, Enum):
    STT = "stt"
    TRANSLATION_MANAGER = "translation_manager"
    ENTERPRISE_TRANSLATION = "enterprise_translation"
    NATURAL_TRANSLATION = "natural_translation"
    TRANSLATION_OPTIMIZER = "translation_optimizer"
    TIMING_OPTIMIZER = "timing_optimizer"
    TTS = "tts"
    AUDIO_BUILDER = "audio_builder"
    FINAL_MUX = "final_mux"


class StageStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"
    STUB = "stub"


@dataclass
class StageDiagnostics:
    processing_ms: float = 0.0
    duration_ms: int = 0
    engine: str = ""
    rules_applied: list[str] = field(default_factory=list)
    quality_score: float | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diff_from_previous: list[dict[str, str]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "processing_ms": self.processing_ms,
            "duration_ms": self.duration_ms,
            "engine": self.engine,
            "rules_applied": list(self.rules_applied),
            "quality_score": self.quality_score,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "diff_from_previous": list(self.diff_from_previous),
            "meta": dict(self.meta),
        }


@dataclass
class StageEnvelope:
    """Immutable-ish carrier between pipeline stages."""

    stage_id: str
    segment_index: int
    text_in: str = ""
    text_out: str = ""
    audio_path: str = ""
    status: str = StageStatus.OK.value
    diagnostics: StageDiagnostics = field(default_factory=StageDiagnostics)
    word_timing_map: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "segment_index": self.segment_index,
            "text_in": self.text_in,
            "text_out": self.text_out,
            "audio_path": self.audio_path,
            "status": self.status,
            "diagnostics": self.diagnostics.to_dict(),
            "word_timing_map": dict(self.word_timing_map),
            "artifacts": dict(self.artifacts),
        }


@dataclass
class SegmentPipelineTrace:
    """Full per-segment pipeline trace for Developer Mode."""

    segment_index: int
    original_text: str = ""
    stages: list[StageEnvelope] = field(default_factory=list)
    word_timing_map: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_index": self.segment_index,
            "original_text": self.original_text,
            "stages": [s.to_dict() for s in self.stages],
            "word_timing_map": dict(self.word_timing_map),
        }


@dataclass
class PipelineContext:
    """Shared read-only context for a dub task (no cross-stage mutation)."""

    task_id: str
    app_dir: str
    src_lang: str
    tgt_lang: str
    segments: list[str] = field(default_factory=list)
    timing_map: list[dict[str, Any]] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def segment_slot_ms(self, index: int) -> int:
        if 0 <= index < len(self.timing_map):
            row = self.timing_map[index]
            if isinstance(row, dict):
                return int(row.get("duration_ms") or row.get("duration") or 0)
            return int(getattr(row, "duration_ms", 0) or 0)
        return 0


class StageModule(ABC):
    """Mandatory interface for every pipeline stage module."""

    stage_id: StageId

    @abstractmethod
    def status(self) -> StageStatus:
        """Module readiness."""

    @abstractmethod
    def run(self, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
        """Process one segment. Must not mutate ctx or other envelopes."""

    def label(self) -> str:
        return self.stage_id.value.replace("_", " ").title()


def timed_run(module: StageModule, ctx: PipelineContext, index: int, envelope_in: StageEnvelope) -> StageEnvelope:
    t0 = time.perf_counter()
    try:
        out = module.run(ctx, index, envelope_in)
        out.diagnostics.processing_ms = round((time.perf_counter() - t0) * 1000, 2)
        return out
    except Exception as exc:
        ms = round((time.perf_counter() - t0) * 1000, 2)
        return StageEnvelope(
            stage_id=module.stage_id.value,
            segment_index=index,
            text_in=envelope_in.text_out or envelope_in.text_in,
            text_out=envelope_in.text_out or envelope_in.text_in,
            status=StageStatus.ERROR.value,
            diagnostics=StageDiagnostics(
                processing_ms=ms,
                errors=[str(exc)],
                engine=module.stage_id.value,
            ),
            word_timing_map=dict(envelope_in.word_timing_map),
        )
