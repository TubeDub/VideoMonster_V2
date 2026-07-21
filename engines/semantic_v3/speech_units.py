"""P42 — Speech Unit / Audio Unit architecture (immutable after stage fix)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


def _uid() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class SpeechUnit:
    """Dub Engine works only with SpeechUnits — never Whisper segments."""

    speech_uuid: str
    sentence_uuid: str
    text: str  # translated speech text (locked meaning)
    source_text: str
    start_ms: int
    end_ms: int
    speaker_uuid: str = ""
    emotion: str = "neutral"
    expected_duration_ms: int = 0
    prediction_confidence: float = 0.0
    version: int = 1

    @property
    def slot_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def evolve(self, **kwargs: Any) -> "SpeechUnit":
        """P50 immutability: changes create a new version."""
        data = self.to_dict()
        data.update(kwargs)
        data["version"] = int(self.version) + 1
        data["speech_uuid"] = self.speech_uuid  # keep identity
        return SpeechUnit(**data)


@dataclass(frozen=True)
class AudioUnit:
    """Scheduler 2.0 (P45) — time/audio only; no text ownership."""

    audio_uuid: str
    speech_uuid: str
    start_ms: int
    end_ms: int
    duration_ms: int
    file: str = ""
    tempo: float = 1.0
    stretch: float = 1.0
    pause_ms: int = 0
    breath_ms: int = 0
    volume: float = 1.0
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def evolve(self, **kwargs: Any) -> "AudioUnit":
        data = self.to_dict()
        data.update(kwargs)
        data["version"] = int(self.version) + 1
        data["audio_uuid"] = self.audio_uuid
        return AudioUnit(**data)


@dataclass
class Timeline:
    """Ordered AudioUnits — segment slots are Scheduler output only."""

    units: list[AudioUnit] = field(default_factory=list)
    timeline_uuid: str = field(default_factory=_uid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeline_uuid": self.timeline_uuid,
            "units": [u.to_dict() for u in self.units],
        }

    def to_timing_map(self) -> list[dict[str, int]]:
        return [{"start": u.start_ms, "end": u.end_ms} for u in self.units]


def speech_units_from_sentences(sentences: list[Any]) -> list[SpeechUnit]:
    out: list[SpeechUnit] = []
    for s in sentences:
        text = str(getattr(s, "translated_text", None) or getattr(s, "text", "") or "")
        src = str(getattr(s, "text", "") or "")
        out.append(
            SpeechUnit(
                speech_uuid=_uid(),
                sentence_uuid=str(getattr(s, "sentence_uuid", "") or _uid()),
                text=text,
                source_text=src,
                start_ms=int(getattr(s, "start_ms", 0) or 0),
                end_ms=int(getattr(s, "end_ms", 0) or 0),
                speaker_uuid=str(getattr(s, "speaker", "") or ""),
                emotion=str(getattr(s, "emotion", "neutral") or "neutral"),
                expected_duration_ms=int(
                    getattr(s, "predicted_tts_ms", 0)
                    or getattr(s, "ideal_duration_ms", 0)
                    or 0
                ),
                prediction_confidence=float(
                    getattr(s, "prediction_confidence", 0.75) or 0.75
                ),
            )
        )
    return out
