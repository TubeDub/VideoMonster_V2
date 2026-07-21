"""StreamDub Engine — shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StreamDubMode(str, Enum):
    FAST = "fast"
    SMART = "smart"
    CINEMA = "cinema"


class QualityGrade(str, Enum):
    GOOD = "GOOD"
    MEDIUM = "MEDIUM"
    BAD = "BAD"


@dataclass
class StreamSegment:
    index: int
    text: str
    start_ms: int = 0
    end_ms: int = 0
    speaker: str | None = None
    pause_after_ms: int = 0
    translated: str = ""
    quality: QualityGrade | None = None
    quality_score: float = 0.0
    quality_issues: list[str] = field(default_factory=list)
    route: str = "fast_mt"
    llm_refined: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class StreamDubRequest:
    project_id: str
    video_path: str
    audio_path: str | None = None
    source_lang: str = "en"
    target_lang: str = "uk"
    mode: StreamDubMode = StreamDubMode.SMART
    voice: str = ""
    model_size: str = "tiny"
    mt_backend: str = "marian"
    max_tokens_per_segment: int = 80


@dataclass
class StreamDubResult:
    project_id: str
    mode: StreamDubMode
    success: bool
    segments: list[StreamSegment] = field(default_factory=list)
    detected_lang: str = ""
    output_audio: str = ""
    output_video: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
