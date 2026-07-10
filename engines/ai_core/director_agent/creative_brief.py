"""CreativeBrief dataclass — per-segment director decisions (READ ONLY downstream)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.ai_core.director_agent.defaults import (
    DEFAULT_BRIEF_VALUES,
    VALID_EMOTIONS,
    VALID_SPEECH_STYLES,
    VALID_SPEAKING_SPEEDS,
    VALID_UTTERANCE_GOALS,
)


def _clamp01(value: Any, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def _clamp_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, v)


@dataclass
class CreativeBrief:
    segment_id: int
    speaker_id: str
    language: str
    emotion: str
    speech_style: str
    speaking_speed: str
    formality: float
    humor: float
    sarcasm: float
    aggression: float
    calmness: float
    emotional_intensity: float
    maximum_duration_ms: int
    preferred_duration_ms: int
    allowed_compression: float
    allowed_expansion: float
    adaptation_priority: float
    meaning_priority: float
    lip_sync_priority: float
    naturalness_priority: float
    utterance_goal: str
    literal_phrasing_importance: float
    deep_semantic_adaptation_needed: bool
    decision_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreativeBrief:
        d = dict(data or {})
        return cls(
            segment_id=_clamp_int(d.get("segment_id"), 0),
            speaker_id=str(d.get("speaker_id") or "default"),
            language=str(d.get("language") or "auto"),
            emotion=str(d.get("emotion") or DEFAULT_BRIEF_VALUES["emotion"]),
            speech_style=str(d.get("speech_style") or DEFAULT_BRIEF_VALUES["speech_style"]),
            speaking_speed=str(d.get("speaking_speed") or DEFAULT_BRIEF_VALUES["speaking_speed"]),
            formality=_clamp01(d.get("formality"), DEFAULT_BRIEF_VALUES["formality"]),
            humor=_clamp01(d.get("humor"), DEFAULT_BRIEF_VALUES["humor"]),
            sarcasm=_clamp01(d.get("sarcasm"), DEFAULT_BRIEF_VALUES["sarcasm"]),
            aggression=_clamp01(d.get("aggression"), DEFAULT_BRIEF_VALUES["aggression"]),
            calmness=_clamp01(d.get("calmness"), DEFAULT_BRIEF_VALUES["calmness"]),
            emotional_intensity=_clamp01(
                d.get("emotional_intensity"), DEFAULT_BRIEF_VALUES["emotional_intensity"]
            ),
            maximum_duration_ms=_clamp_int(d.get("maximum_duration_ms"), 1000, minimum=1),
            preferred_duration_ms=_clamp_int(d.get("preferred_duration_ms"), 1000, minimum=1),
            allowed_compression=_clamp01(
                d.get("allowed_compression"), DEFAULT_BRIEF_VALUES["allowed_compression"]
            ),
            allowed_expansion=_clamp01(
                d.get("allowed_expansion"), DEFAULT_BRIEF_VALUES["allowed_expansion"]
            ),
            adaptation_priority=_clamp01(
                d.get("adaptation_priority"), DEFAULT_BRIEF_VALUES["adaptation_priority"]
            ),
            meaning_priority=_clamp01(
                d.get("meaning_priority"), DEFAULT_BRIEF_VALUES["meaning_priority"]
            ),
            lip_sync_priority=_clamp01(
                d.get("lip_sync_priority"), DEFAULT_BRIEF_VALUES["lip_sync_priority"]
            ),
            naturalness_priority=_clamp01(
                d.get("naturalness_priority"), DEFAULT_BRIEF_VALUES["naturalness_priority"]
            ),
            utterance_goal=str(d.get("utterance_goal") or DEFAULT_BRIEF_VALUES["utterance_goal"]),
            literal_phrasing_importance=_clamp01(
                d.get("literal_phrasing_importance"),
                DEFAULT_BRIEF_VALUES["literal_phrasing_importance"],
            ),
            deep_semantic_adaptation_needed=bool(
                d.get("deep_semantic_adaptation_needed", DEFAULT_BRIEF_VALUES["deep_semantic_adaptation_needed"])
            ),
            decision_reasons=list(d.get("decision_reasons") or []),
        )

    def validate_enums(self) -> list[str]:
        issues: list[str] = []
        if self.emotion not in VALID_EMOTIONS:
            issues.append(f"invalid_emotion:{self.emotion}")
        if self.speech_style not in VALID_SPEECH_STYLES:
            issues.append(f"invalid_speech_style:{self.speech_style}")
        if self.speaking_speed not in VALID_SPEAKING_SPEEDS:
            issues.append(f"invalid_speaking_speed:{self.speaking_speed}")
        if self.utterance_goal not in VALID_UTTERANCE_GOALS:
            issues.append(f"invalid_utterance_goal:{self.utterance_goal}")
        return issues


__all__ = ["CreativeBrief"]
