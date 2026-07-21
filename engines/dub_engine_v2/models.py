"""Dub Engine 2.0 models — Speech Unit / Audio Unit / Timeline (Part 5)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


def _uid() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class SpeechUnitV2:
    """P402 — primary Dub Engine unit after Semantic Lock."""

    speech_uuid: str
    sentence_uuid: str
    speaker_uuid: str
    scene_uuid: str
    text: str  # locked translation — immutable reference
    source_text: str
    emotion: str = "neutral"
    style: str = ""
    prosody: str = ""
    expected_duration: int = 0
    predicted_duration: int = 0
    priority: float = 0.5
    speech_status: str = "planned"  # planned|ready|synthesized|failed
    start_ms: int = 0
    end_ms: int = 0
    decision_steps: tuple[str, ...] = ()
    version: int = 1

    @property
    def slot_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["decision_steps"] = list(self.decision_steps)
        d["slot_ms"] = self.slot_ms
        return d

    def evolve(self, **kwargs: Any) -> "SpeechUnitV2":
        data = self.to_dict()
        data.pop("slot_ms", None)
        data.update(kwargs)
        if "decision_steps" in data and not isinstance(data["decision_steps"], tuple):
            data["decision_steps"] = tuple(data["decision_steps"])
        data["version"] = int(self.version) + 1
        data["speech_uuid"] = self.speech_uuid
        data["text"] = self.text  # never change locked text via evolve
        return SpeechUnitV2(**data)


@dataclass(frozen=True)
class AudioUnitV2:
    """P403 — Scheduler works only with Audio Units."""

    audio_uuid: str
    speech_uuid: str
    wav_path: str = ""
    duration: int = 0
    sample_rate: int = 24000
    channels: int = 1
    start_ms: int = 0
    end_ms: int = 0
    alignment: tuple[dict[str, Any], ...] = ()
    quality: float = 1.0
    audio_state: str = "planned"  # planned|ready|validated|failed
    tempo: float = 1.0
    stretch: float = 1.0
    pause_ms: int = 0
    breath_ms: int = 0
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["alignment"] = list(self.alignment)
        return d

    def evolve(self, **kwargs: Any) -> "AudioUnitV2":
        data = self.to_dict()
        data.update(kwargs)
        if "alignment" in data and not isinstance(data["alignment"], tuple):
            data["alignment"] = tuple(data["alignment"])
        data["version"] = int(self.version) + 1
        data["audio_uuid"] = self.audio_uuid
        return AudioUnitV2(**data)


@dataclass
class ProjectTimeline:
    """P409 — single project timeline (no local timelines)."""

    timeline_uuid: str = field(default_factory=_uid)
    units: list[AudioUnitV2] = field(default_factory=list)
    pauses: list[dict[str, int]] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeline_uuid": self.timeline_uuid,
            "units": [u.to_dict() for u in self.units],
            "pauses": list(self.pauses),
            "transitions": list(self.transitions),
            "conflicts": list(self.conflicts),
        }

    def to_timing_map(self) -> list[dict[str, int]]:
        return [{"start": u.start_ms, "end": u.end_ms} for u in self.units]


@dataclass
class AudioPlan:
    """P401 — pre-TTS plan for one speech unit."""

    speech_uuid: str
    duration_ms: int
    duration_min_ms: int
    duration_max_ms: int
    tempo_min: float
    tempo_max: float
    stretch_min: float
    stretch_max: float
    available_ms: int
    neighbor_before: str = ""
    neighbor_after: str = ""
    conflict_risks: list[str] = field(default_factory=list)
    strategy_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DubMetrics:
    """P417 — project-level audio metrics."""

    overlap_count: int = 0
    tail_spill_count: int = 0
    borrow_time_count: int = 0
    tempo_usage: int = 0
    stretch_usage: int = 0
    merge_usage: int = 0
    manual_review_count: int = 0
    prediction_error: float = 0.0
    speech_flow_score: float = 100.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
