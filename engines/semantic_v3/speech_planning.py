"""P110 — Speech Planning: pre-TTS plan for each MeaningUnit.

Before TTS launch, the system must already know:
- expected duration
- expected tempo
- expected pauses
- expected breaths
- overflow probability
- underflow probability
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.semantic_v3.speech_planning")


@dataclass
class SpeechPlan:
    """Pre-TTS speech plan for a MeaningUnit."""

    unit_uuid: str = ""
    text: str = ""

    expected_duration_ms: int = 0
    slot_ms: int = 0
    expected_tempo: float = 1.0
    expected_pauses: list[int] = field(default_factory=list)
    expected_breaths: list[int] = field(default_factory=list)

    overflow_probability: float = 0.0
    underflow_probability: float = 0.0
    overflow_ms: int = 0
    underflow_ms: int = 0

    tts_ready: bool = False
    tts_rate: str = ""
    tts_pitch: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_uuid": self.unit_uuid,
            "text": self.text,
            "expected_duration_ms": self.expected_duration_ms,
            "slot_ms": self.slot_ms,
            "expected_tempo": self.expected_tempo,
            "expected_pauses": self.expected_pauses,
            "expected_breaths": self.expected_breaths,
            "overflow_probability": self.overflow_probability,
            "underflow_probability": self.underflow_probability,
            "overflow_ms": self.overflow_ms,
            "underflow_ms": self.underflow_ms,
            "tts_ready": self.tts_ready,
            "tts_rate": self.tts_rate,
            "tts_pitch": self.tts_pitch,
            "warnings": self.warnings,
        }


def build_speech_plan(
    unit: Any,
    *,
    predicted_duration_ms: int = 0,
    slot_ms: int = 0,
    lang: str = "uk",
) -> SpeechPlan:
    """P110: build pre-TTS speech plan for a MeaningUnit."""
    text = getattr(unit, 'translated_text', '') or getattr(unit, 'text', '') or ''
    uid = getattr(unit, 'unit_uuid', '') or ''
    actual_slot = slot_ms or getattr(unit, 'slot_ms', 0) or 0
    predicted = predicted_duration_ms or getattr(unit, 'predicted_duration_ms', 0) or 0

    plan = SpeechPlan(
        unit_uuid=uid,
        text=text,
        expected_duration_ms=predicted,
        slot_ms=actual_slot,
    )

    if not text.strip():
        plan.tts_ready = False
        plan.warnings.append("empty_text")
        return plan

    if actual_slot > 0 and predicted > 0:
        ratio = predicted / actual_slot
        if ratio > 1.0:
            plan.overflow_ms = predicted - actual_slot
            plan.overflow_probability = min(1.0, (ratio - 1.0) * 2)
            plan.expected_tempo = min(1.3, ratio)
            plan.tts_rate = f"+{int((plan.expected_tempo - 1.0) * 100)}%"
        elif ratio < 0.7:
            plan.underflow_ms = actual_slot - predicted
            plan.underflow_probability = min(1.0, (1.0 - ratio) * 1.5)
            plan.expected_tempo = max(0.8, ratio)
            plan.tts_rate = f"-{int((1.0 - plan.expected_tempo) * 100)}%"
        else:
            plan.expected_tempo = 1.0
            plan.tts_rate = "-5%"

    pause_positions = []
    breath_positions = []
    words = text.split()
    pos_ms = 0
    syllable_rate = 170

    for i, word in enumerate(words):
        syllables = max(1, sum(1 for c in word.lower() if c in 'aeiouyаеєиіїоуюяё'))
        word_ms = int(syllables * syllable_rate / max(0.5, plan.expected_tempo))
        pos_ms += word_ms

        if word.endswith(('.', '!', '?', ';', ':')):
            pause_positions.append(pos_ms)
            if i > 0 and i % 8 == 0:
                breath_positions.append(pos_ms)
        elif word.endswith(','):
            pause_positions.append(pos_ms)

    plan.expected_pauses = pause_positions
    plan.expected_breaths = breath_positions

    if plan.overflow_probability > 0.5:
        plan.warnings.append(f"high_overflow_risk: {plan.overflow_ms}ms over")
    if plan.underflow_probability > 0.5:
        plan.warnings.append(f"high_underflow_risk: {plan.underflow_ms}ms under")
    if plan.expected_tempo > 1.2:
        plan.warnings.append(f"fast_tempo: {plan.expected_tempo:.2f}x")

    plan.tts_ready = True

    logger.info(
        "SpeechPlan: unit=%s dur=%dms slot=%dms tempo=%.2f overflow=%.0f%% underflow=%.0f%%",
        uid[:8], predicted, actual_slot,
        plan.expected_tempo,
        plan.overflow_probability * 100,
        plan.underflow_probability * 100,
    )
    return plan


def build_speech_plans(
    units: list[Any],
    *,
    lang: str = "uk",
) -> list[SpeechPlan]:
    """Build speech plans for all MeaningUnits."""
    plans = []
    for unit in units:
        plan = build_speech_plan(unit, lang=lang)

        if hasattr(unit, 'expected_tempo'):
            unit.expected_tempo = plan.expected_tempo
        if hasattr(unit, 'expected_pauses'):
            unit.expected_pauses = plan.expected_pauses
        if hasattr(unit, 'expected_breaths'):
            unit.expected_breaths = plan.expected_breaths
        if hasattr(unit, 'overflow_probability'):
            unit.overflow_probability = plan.overflow_probability
        if hasattr(unit, 'underflow_probability'):
            unit.underflow_probability = plan.underflow_probability

        plans.append(plan)
    return plans
