"""P401 Audio Planning + P402 Speech Unit builder (post Semantic Lock)."""

from __future__ import annotations

import uuid
from typing import Any

from engines.dub_engine_v2.models import AudioPlan, SpeechUnitV2
from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.duration_predictor import predict_speech_duration
from engines.semantic_v3.types import SemanticSentence


def _uid() -> str:
    return uuid.uuid4().hex


def assert_text_locked(sent: SemanticSentence) -> None:
    if not (sent.semantic_locked or getattr(sent, "lock_status", "") == "locked"):
        # Allow planned path when translate=False lock_all was used
        if not sent.translated_text and not sent.text:
            raise ArchitectureViolation(
                "P401: empty speech text",
                stage="dub_engine_v2",
                rule="audio_planning",
            )


def speech_units_from_locked_sentences(
    sentences: list[SemanticSentence],
    *,
    voice: str = "default",
) -> list[SpeechUnitV2]:
    """Convert locked SemanticSentence → SpeechUnitV2 (text is reference only)."""
    units: list[SpeechUnitV2] = []
    for s in sentences:
        assert_text_locked(s)
        text = (s.translated_text or s.text or "").strip()
        pred = predict_speech_duration(
            text,
            voice=voice,
            emotion=s.emotion or "neutral",
            speech_rate=float(s.speech_rate or 1.0),
        )
        steps: tuple[str, ...] = ()
        rec = getattr(s, "decision_record", None)
        if rec and getattr(rec, "accepted", None):
            steps = tuple(rec.accepted.steps or ())
        elif s.recovery_plan:
            steps = tuple(s.recovery_plan)
        units.append(
            SpeechUnitV2(
                speech_uuid=_uid(),
                sentence_uuid=s.sentence_uuid,
                speaker_uuid=s.speaker or getattr(s, "speaker_uuid", "") or "",
                scene_uuid=getattr(s, "scene_uuid", "") or "",
                text=text,
                source_text=s.text or "",
                emotion=s.emotion or "neutral",
                style=getattr(s, "style", "") or "",
                prosody="planned",
                expected_duration=int(s.predicted_tts_ms or pred.expected_ms),
                predicted_duration=int(pred.expected_ms),
                priority=0.8 if s.is_dialogue else 0.5,
                speech_status="planned",
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                decision_steps=steps,
            )
        )
        # Keep sentence predicted in sync (timing meta only, not text)
        s.predicted_tts_ms = int(pred.expected_ms)
        setattr(s, "prediction_confidence", pred.confidence)
    return units


def build_audio_plans(
    speech_units: list[SpeechUnitV2],
    *,
    tempo_max: float = 1.12,
    stretch_max: float = 1.05,
) -> list[AudioPlan]:
    """P401 — full pre-TTS plan for every replica."""
    plans: list[AudioPlan] = []
    for i, su in enumerate(speech_units):
        slot = max(1, su.slot_ms)
        pred = int(su.predicted_duration or su.expected_duration or slot)
        risks: list[str] = []
        if pred > int(slot * 1.08):
            risks.append("overflow")
        if pred < int(slot * 0.85):
            risks.append("underflow")
        nbr_b = speech_units[i - 1].speech_uuid if i > 0 else ""
        nbr_a = speech_units[i + 1].speech_uuid if i + 1 < len(speech_units) else ""
        if i + 1 < len(speech_units):
            nxt = speech_units[i + 1]
            if su.start_ms + pred > nxt.start_ms + 40:
                risks.append("neighbor_intersect")
        plans.append(
            AudioPlan(
                speech_uuid=su.speech_uuid,
                duration_ms=pred,
                duration_min_ms=int(pred * 0.85),
                duration_max_ms=int(pred * 1.15),
                tempo_min=0.95,
                tempo_max=tempo_max,
                stretch_min=0.95,
                stretch_max=stretch_max,
                available_ms=slot,
                neighbor_before=nbr_b,
                neighbor_after=nbr_a,
                conflict_risks=risks,
                strategy_steps=list(su.decision_steps),
            )
        )
    return plans
