"""Meaning Fit Engine V2 — Target Duration → best natural expression.

Meaning Preservation (July 2026): after variant selection, gate with
Meaning Coverage / Entity / Event / Sentence Integrity. If adaptation
destroys meaning → fall back to pre-adaptation translation (direct).
Beauty never beats coverage.
"""

from __future__ import annotations

import logging
from typing import Any

from engines.semantic_v3.duration_predictor import predict_speech_duration
from engines.semantic_v3.meaning_preservation import (
    build_semantic_event_graph,
    gate_adaptation_text,
)
from engines.semantic_v3.meaning_unit_builder import build_meaning_units
from engines.semantic_v3.semantic_adaptation import generate_adaptation_variants
from engines.semantic_v3.stage_validator import validate_meaning_preservation
from engines.semantic_v3.strategy_selection import score_all_variants, select_best
from engines.semantic_v3.target_duration_engine import compute_target_duration
from engines.semantic_v3.types import SemanticSentence
from engines.semantic_v3.variant_duration_predictor import compute_duration_score

logger = logging.getLogger("tubedub.semantic_v3.meaning_fit_engine")


def fit_meaning_units_to_target(
    sentences: list[SemanticSentence],
    *,
    voice: str = "default",
    tgt_lang: str = "uk",
) -> list[SemanticSentence]:
    """Fit unlocked translated MeaningUnits to source-cadence Target Duration."""
    units = build_meaning_units(sentences)
    fitted: list[SemanticSentence] = []

    for unit in units:
        baseline = " ".join(
            (sentence.translated_text or sentence.text or "").strip()
            for sentence in unit.sentences
        ).strip()
        unit.translated_text = baseline
        target = compute_target_duration(unit, translated_text=unit.translated_text)
        event_graph = build_semantic_event_graph(unit.text)

        variants = generate_adaptation_variants(
            unit,
            translated_text=unit.translated_text,
            source_text=unit.text,
            slot_ms=target.target_ms,
            tgt_lang=tgt_lang,
            style=unit.speech_style,
            emotion=unit.emotion,
        )

        for variant in variants:
            prediction = predict_speech_duration(
                variant.text,
                voice=voice,
                emotion=unit.emotion,
            )
            variant.predicted_duration_ms = prediction.expected_ms
            variant.prediction_confidence = prediction.confidence
            variant.duration_score = compute_duration_score(
                prediction.expected_ms,
                target.target_ms,
                tolerance_pct=target.tolerance_ms * 100.0 / max(1, target.target_ms),
            )

        variants = score_all_variants(
            variants,
            source_text=unit.text,
            slot_ms=target.target_ms,
            emotion=unit.emotion,
            style=unit.speech_style,
            is_dialogue=any(sentence.is_dialogue for sentence in unit.sentences),
            speaker=unit.speaker,
            tgt_lang=tgt_lang,
        )
        best = select_best(variants)
        if best is None:
            fitted.extend(unit.sentences)
            continue

        validation = validate_meaning_preservation(
            unit.text,
            best.text,
            entities=[
                entity
                for sentence in unit.sentences
                for entity in (sentence.entities or [])
            ],
        )
        if not validation.passed:
            direct = next(
                (variant for variant in variants if variant.strategy == "direct"),
                None,
            )
            if direct is not None:
                best = direct

        # Meaning Preservation gate — reject compressors / truncated variants
        final_text, mp_report = gate_adaptation_text(
            source=unit.text,
            adapted=best.text,
            baseline=baseline or best.text,
        )
        # region agent log
        try:
            import json
            import time
            from pathlib import Path

            _dbg = Path(__file__).resolve().parents[2] / "debug-e4d146.log"
            with _dbg.open("a", encoding="utf-8") as _f:
                _f.write(
                    json.dumps(
                        {
                            "sessionId": "e4d146",
                            "runId": "meaning-preservation-v2",
                            "hypothesisId": "MP1,MP2,MP3",
                            "location": "meaning_fit_engine.py:gate",
                            "message": "Meaning preservation gate",
                            "data": {
                                "unitId": getattr(unit, "unit_id", "?"),
                                "srcWords": len((unit.text or "").split()),
                                "adaptedWords": len((best.text or "").split()),
                                "finalWords": len((final_text or "").split()),
                                "fallback": bool(mp_report.fallback),
                                "coverage": mp_report.coverage,
                                "entityScore": mp_report.entity_preservation_score,
                                "eventScore": mp_report.event_preservation_score,
                                "narrativeOk": mp_report.narrative_passed,
                                "sentenceOk": mp_report.sentence_integrity_passed,
                                "reasons": list(mp_report.reasons)[:6],
                                "strategy": getattr(best, "strategy", ""),
                            },
                            "timestamp": int(time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass
        # endregion
        if mp_report.fallback:
            logger.warning(
                "MeaningPreservation FALLBACK unit=%s reasons=%s",
                getattr(unit, "unit_id", "?"),
                mp_report.reasons[:5],
            )

        # MeaningUnit is now the text owner. A multi-sentence continuing thought
        # becomes one atomic downstream SemanticSentence rather than duplicated text.
        sentence = unit.sentences[0]
        if len(unit.sentences) > 1:
            sentence.text = unit.text
            sentence.words = list(unit.words)
            sentence.end_ms = unit.end_ms
            sentence.is_complex = True
        sentence.translated_text = final_text
        sentence.predicted_tts_ms = best.predicted_duration_ms
        setattr(sentence, "prediction_confidence", best.prediction_confidence)
        setattr(sentence, "target_duration", target.to_dict())
        setattr(sentence, "adaptation_variants", [variant.to_dict() for variant in variants])
        setattr(sentence, "selected_variant_id", best.variant_id)
        setattr(sentence, "meaning_fit_selected", True)
        setattr(sentence, "meaning_preservation", mp_report.to_trace_dict())
        setattr(sentence, "semantic_event_graph", event_graph.to_dict())
        setattr(
            sentence,
            "meaning_preservation_fallback",
            bool(mp_report.fallback),
        )
        fitted.append(sentence)

    logger.info(
        "MeaningFitV2: sentences=%d meaning_units=%d fitted=%d",
        len(sentences),
        len(units),
        len(fitted),
    )
    return fitted
