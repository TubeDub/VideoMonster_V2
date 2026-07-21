"""Meaning Fit V2 — compute a speech target, not merely an available slot."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from engines.semantic_v3.variant_duration_predictor import count_syllables


@dataclass(frozen=True)
class TargetDuration:
    target_ms: int
    available_ms: int
    source_speech_ms: int
    source_syllables: int
    target_syllables: int
    speaker_syllables_per_second: float
    tolerance_ms: int
    confidence: float
    method: str = "source_cadence_available_window"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def target_ms_for(unit: Any) -> int:
    """Return semantic speech target; fall back to the physical slot."""
    raw = getattr(unit, "target_duration", None)
    if isinstance(raw, dict) and int(raw.get("target_ms") or 0) > 0:
        return int(raw["target_ms"])
    return max(0, int(getattr(unit, "slot_ms", 0) or 0))


def compute_target_duration(
    unit: Any,
    *,
    translated_text: str = "",
    tolerance_pct: float = 12.0,
) -> TargetDuration:
    """Derive Target Duration from source cadence and the available window.

    Implausibly slow/fast ASR timing is clamped to natural speech cadence. This
    prevents a one-word reply from being expanded merely because its technical
    ASR slot includes surrounding silence.
    """
    source_text = str(getattr(unit, "text", "") or "")
    target_text = str(
        translated_text or getattr(unit, "translated_text", "") or source_text
    )
    available_ms = max(0, int(getattr(unit, "slot_ms", 0) or 0))

    words = list(getattr(unit, "words", []) or [])
    if words:
        speech_start = min(int(getattr(word, "start_ms", 0) or 0) for word in words)
        speech_end = max(int(getattr(word, "end_ms", 0) or 0) for word in words)
        source_speech_ms = max(1, speech_end - speech_start)
    else:
        source_speech_ms = max(1, available_ms)

    source_syllables = max(1, count_syllables(source_text))
    target_syllables = max(1, count_syllables(target_text))
    observed_rate = source_syllables * 1000.0 / source_speech_ms
    speaker_rate = min(7.0, max(2.0, observed_rate))

    punctuation_ms = (
        source_text.count(",") * 90
        + source_text.count(";") * 140
        + sum(source_text.count(mark) for mark in ".!?") * 160
    )
    cadence_ms = int(round(source_syllables * 1000.0 / speaker_rate + punctuation_ms))
    if available_ms > 0:
        target_ms = min(cadence_ms, available_ms)
    else:
        target_ms = cadence_ms
    target_ms = max(180, target_ms)

    tolerance_ms = max(120, int(round(target_ms * max(0.01, tolerance_pct / 100.0))))
    confidence = 0.9 if words and observed_rate >= 2.0 else 0.72
    return TargetDuration(
        target_ms=target_ms,
        available_ms=available_ms,
        source_speech_ms=source_speech_ms,
        source_syllables=source_syllables,
        target_syllables=target_syllables,
        speaker_syllables_per_second=round(speaker_rate, 3),
        tolerance_ms=tolerance_ms,
        confidence=confidence,
    )
