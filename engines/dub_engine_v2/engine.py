"""Dub Engine 2.0 orchestrator — post Semantic Lock audio path only."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from engines.dub_engine_v2.detectors import (
    coordinate_multi_voice,
    detect_overlaps,
    detect_tail_spill,
)
from engines.dub_engine_v2.models import DubMetrics, ProjectTimeline, SpeechUnitV2
from engines.dub_engine_v2.planning import build_audio_plans, speech_units_from_locked_sentences
from engines.dub_engine_v2.quality import (
    build_lipsync_foundation,
    speech_flow_score,
    validate_audio_units,
)
from engines.dub_engine_v2.scheduler import schedule_project
from engines.dub_engine_v2.timing import optimize_timing
from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.types import SemanticSentence

logger = logging.getLogger("tubedub.dub_engine_v2")


@dataclass
class DubEngineResult:
    speech_units: list[SpeechUnitV2]
    audio_plans: list[Any]
    adjustments: list[Any]
    timeline: ProjectTimeline
    lipsync: dict[str, Any]
    metrics: DubMetrics
    multi_voice: list[dict[str, Any]] = field(default_factory=list)
    escalations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "speech_units": [u.to_dict() for u in self.speech_units],
            "audio_plans": [p.to_dict() for p in self.audio_plans],
            "adjustments": [a.to_dict() for a in self.adjustments],
            "timeline": self.timeline.to_dict(),
            "lipsync": {k: v.to_dict() for k, v in self.lipsync.items()},
            "metrics": self.metrics.to_dict(),
            "multi_voice": list(self.multi_voice),
            "escalations": dict(self.escalations),
        }


def run_dub_engine(
    sentences: list[SemanticSentence],
    *,
    voice: str = "default",
    profile: str = "",
    hard_fail_overlap: bool = True,
    require_wav_files: bool = False,
) -> DubEngineResult:
    """
    Audio Planning → Predict → ATO → Scheduler → Detectors → LipSync → QA.
    Never mutates locked translation text.
    """
    # Snapshot texts for immutability proof
    before = [(s.sentence_uuid, s.translated_text) for s in sentences]

    speech = speech_units_from_locked_sentences(sentences, voice=voice)
    plans = build_audio_plans(speech)

    adjustments = []
    metrics = DubMetrics()
    for su, plan in zip(speech, plans):
        adj = optimize_timing(su, plan, strategy_steps=list(su.decision_steps))
        adjustments.append(adj)
        if "tempo" in adj.steps_applied:
            metrics.tempo_usage += 1
        if "micro_stretch" in adj.steps_applied:
            metrics.stretch_usage += 1
        if adj.borrow_ms > 0:
            metrics.borrow_time_count += 1
        if "sentence_merge" in adj.steps_applied:
            metrics.merge_usage += 1
        if "manual_review" in adj.steps_applied:
            metrics.manual_review_count += 1
        err = abs(adj.expected_duration_ms - plan.available_ms) / max(1, plan.available_ms)
        metrics.prediction_error += err

    if speech:
        metrics.prediction_error = round(metrics.prediction_error / len(speech), 3)

    lipsync = build_lipsync_foundation(speech)
    align_map = {
        sid: tuple(b.visemes) for sid, b in lipsync.items()
    }
    timeline = schedule_project(speech, adjustments, lipsync_alignment=align_map)

    # Detectors
    try:
        overlaps = detect_overlaps(timeline, hard_fail=hard_fail_overlap)
    except ArchitectureViolation:
        metrics.overlap_count = 1
        raise
    metrics.overlap_count = len(overlaps)

    try:
        spills = detect_tail_spill(speech, timeline, hard_fail=True)
    except ArchitectureViolation:
        metrics.tail_spill_count = 1
        raise
    metrics.tail_spill_count = len(spills)

    mv = coordinate_multi_voice(speech, timeline)
    metrics.speech_flow_score = speech_flow_score(speech, timeline.units)

    # Planned audio validation (files optional until TTS)
    validate_audio_units(timeline.units, require_files=require_wav_files)

    escalations: dict[str, Any] = {}
    needs = [a for a in adjustments if a.needs_decision and a.overflow]
    if needs:
        from engines.dub_engine_v2.conflicts import resolve_conflicts_via_decision

        escalations = resolve_conflicts_via_decision(
            sentences,
            [{"type": "overflow", "speech_uuid": a.speech_uuid} for a in needs],
            profile=profile,
        )

    # Immutability check
    after = [(s.sentence_uuid, s.translated_text) for s in sentences]
    if before != after:
        raise ArchitectureViolation(
            "P420: Dub Engine mutated locked translation text",
            stage="dub_engine_v2",
            rule="no_text_mutation",
        )

    logger.info(
        "DubEngine2: speech=%d audio=%d flow=%.1f overlaps=%d",
        len(speech),
        len(timeline.units),
        metrics.speech_flow_score,
        metrics.overlap_count,
    )
    return DubEngineResult(
        speech_units=speech,
        audio_plans=plans,
        adjustments=adjustments,
        timeline=timeline,
        lipsync=lipsync,
        metrics=metrics,
        multi_voice=mv,
        escalations=escalations,
    )
