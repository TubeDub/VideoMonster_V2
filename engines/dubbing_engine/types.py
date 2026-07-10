"""Shared data types for the unified Dubbing Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageLog:
    """What one pipeline stage did to a segment."""
    stage: str          # "entity" | "adapt" | "punct" | "stress" | "voice" | "timing" | "validate"
    applied: bool
    before: str
    after: str
    note: str = ""
    elapsed_ms: float = 0.0


@dataclass
class EntityInfo:
    """Named entity found in the source text."""
    text: str               # Surface form in source
    label: str              # PERSON | BRAND | CAR | ORG | GEO | OTHER
    translation: str = ""   # Expected surface form in target language (may equal text)
    protected: bool = True  # If True, must survive adaptation unchanged


@dataclass
class DubbingSegment:
    """Input to the engine for one segment."""
    index: int
    original_text: str          # Whisper / source-language text (English)
    translated_text: str        # Machine-translated text (target language)
    slot_start_ms: int          # Timeline start
    slot_end_ms: int            # Timeline end
    entities: list[EntityInfo] = field(default_factory=list)

    @property
    def slot_ms(self) -> int:
        return max(0, self.slot_end_ms - self.slot_start_ms)


@dataclass
class DubbingResult:
    """Output of the engine for one segment."""
    index: int
    original_text: str          # Source (English)
    input_text: str             # Translation (target lang) before engine
    output_text: str            # Final text ready for TTS
    passed_validation: bool     # If False → TTS is skipped for this segment
    validation_notes: list[str] = field(default_factory=list)
    stage_log: list[StageLog] = field(default_factory=list)

    # Timing guidance for audio-fitting
    predicted_ms: int = 0
    slot_ms: int = 0
    natural_pause_ms: int = 120
    recommended_strategy: str = "direct"
    # "direct"         → no adaptation needed
    # "adapted"        → text was shortened/reframed
    # "merge_next"     → should merge with next segment
    # "video_adapt"    → cannot fit in text; ask video to stretch slightly
    # "skip_tts"       → validation failed, leave silent

    # Per-stage booleans (convenient for logging/UI)
    entity_ok: bool = True
    punct_ok: bool = True
    stress_ok: bool = True
    timing_ok: bool = True
    voice_ok: bool = True
    lang_ok: bool = True
    meaning_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "original": self.original_text[:80],
            "input": self.input_text[:80],
            "output": self.output_text[:80],
            "passed": self.passed_validation,
            "strategy": self.recommended_strategy,
            "predicted_ms": self.predicted_ms,
            "slot_ms": self.slot_ms,
            "stages": [{"s": s.stage, "applied": s.applied, "note": s.note} for s in self.stage_log],
            "notes": self.validation_notes,
        }
