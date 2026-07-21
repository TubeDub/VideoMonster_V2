"""Meaning-First Pipeline V2 — full orchestrator (P101–P120).

Pipeline:
    Whisper → Word Archive → Sentence Reconstruction → MeaningUnit Builder →
    Context Graph → Translation → Semantic Adaptation → Meaning Validation →
    Translation LOCK → Speech Planning → Duration Prediction → TTS →
    Audio Optimization → Scheduler → Merge → Render

Whisper is ONLY the ASR source. After Word Archive, Whisper segments are
forbidden (P116). All decisions use MeaningUnit as the primary pipeline unit.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.semantic_v3.meaning_first_pipeline")
_DEBUG_LOG_PATH = Path(r"C:\Users\serhii\Desktop\VideoMonster_V2\debug-7e57dc.log")


def _debug_log(
    hypothesis_id: str, location: str, message: str, data: dict[str, Any]
) -> None:
    """Temporary debug-mode NDJSON logger; emits only non-content metrics."""
    try:
        payload = {
            "sessionId": "7e57dc",
            "runId": "meaning-fit-baseline",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run_meaning_first_pipeline(
    asr_texts: list[str],
    asr_timing: list[Any],
    *,
    word_maps: list[Any] | None = None,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    voice: str = "default",
    translate: bool = True,
    translate_fn: Callable[[str, str, str], str] | None = None,
    app_dir: Any = None,
    place: str = "",
    content_mode: str = "",
    style_hint: str = "",
    terminology: list[str] | None = None,
    lock: bool = True,
) -> dict[str, Any]:
    """Run the complete Meaning-First Pipeline V2.

    Returns a dict with:
        words, sentences, meaning_units, speech_plans, validation, meta
    """
    from engines.semantic_v3.types import SemanticProject

    # ── P101: Word Archive ──────────────────────────────────────────────
    archive = _build_asr_archive(asr_texts, asr_timing)

    from engines.semantic_v3.word_engine import build_words_from_timing_map
    from engines.semantic_v3.word_model import assert_word_model_complete, enrich_word_model

    words = build_words_from_timing_map(asr_texts, asr_timing, word_maps)
    words = enrich_word_model(words, language=src_lang)
    assert_word_model_complete(words)
    original_word_count = len(words)

    logger.info("P101 Word Archive: %d words from %d ASR segments", len(words), len(asr_texts))

    # ── P102: Sentence Reconstruction ───────────────────────────────────
    from engines.semantic_v3.boundary_optimizer import optimize_boundaries
    from engines.semantic_v3.sentence_builder import assert_sentences_atomic
    from engines.semantic_v3.sentence_confidence import apply_sentence_confidence

    sentences = optimize_boundaries(words)
    assert_sentences_atomic(sentences)
    sentences = apply_sentence_confidence(sentences)

    logger.info("P102 Sentence Reconstruction: %d sentences", len(sentences))

    # ── P103: MeaningUnit Builder ───────────────────────────────────────
    from engines.semantic_v3.meaning_unit_builder import build_meaning_units

    meaning_units = build_meaning_units(sentences)

    logger.info("P103 MeaningUnit Builder: %d meaning units from %d sentences",
                len(meaning_units), len(sentences))
    # region agent log
    _debug_log(
        "H1",
        "meaning_first_pipeline.py:98",
        "MeaningUnit timing inputs before adaptation",
        {
            "unitCount": len(meaning_units),
            "units": [
                {
                    "slotMs": unit.slot_ms,
                    "sentenceCount": len(unit.sentences),
                    "speakerPresent": bool(unit.speaker),
                    "scenePresent": bool(
                        any(getattr(sentence, "scene_uuid", "") for sentence in unit.sentences)
                    ),
                }
                for unit in meaning_units
            ],
        },
    )
    # endregion

    # ── P119 gate: word_archive → meaning_unit_builder ──────────────────
    _validate_gate(
        "sentence_reconstruction", "meaning_unit_builder",
        words=words, sentences=sentences, meaning_units=meaning_units,
        original_word_count=original_word_count,
    )

    # ── P104: Context Graph ─────────────────────────────────────────────
    from engines.semantic_v3.context_graph import build_context_graph

    meaning_units = build_context_graph(meaning_units)

    logger.info("P104 Context Graph: enriched %d units", len(meaning_units))

    # ── P116: No Segment Rule check ─────────────────────────────────────
    from engines.semantic_v3.stage_validator import validate_no_segment_rule

    segment_violations = validate_no_segment_rule(meaning_units)
    if segment_violations:
        logger.warning("P116 violations: %s", segment_violations)

    # ── P105: Translation ───────────────────────────────────────────────
    if translate:
        meaning_units = _translate_meaning_units(
            meaning_units,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            translate_fn=translate_fn,
            app_dir=app_dir,
        )
        logger.info("P105 Translation: completed for %d units", len(meaning_units))

    # ── P106: Semantic Adaptation ───────────────────────────────────────
    from engines.semantic_v3.semantic_adaptation import (
        generate_adaptation_variants,
        select_best_variant,
    )

    for unit in meaning_units:
        variants = generate_adaptation_variants(
            unit,
            translated_text=unit.translated_text,
            source_text=unit.text,
            slot_ms=unit.slot_ms,
            tgt_lang=tgt_lang,
            style=unit.speech_style,
            emotion=unit.emotion,
        )

        if variants:
            # ── P107: Duration Prediction per variant ───────────────────
            from engines.semantic_v3.variant_duration_predictor import (
                compute_duration_score,
                predict_all_variants,
            )

            variants = predict_all_variants(variants, lang=tgt_lang)

            for var in variants:
                if unit.slot_ms > 0 and var.predicted_duration_ms > 0:
                    var.duration_score = compute_duration_score(
                        var.predicted_duration_ms, unit.slot_ms,
                    )

            # ── P108: Strategy Selection ────────────────────────────────
            from engines.semantic_v3.strategy_selection import score_all_variants, select_best

            variants = score_all_variants(
                variants,
                source_text=unit.text,
                slot_ms=unit.slot_ms,
                emotion=unit.emotion,
                style=unit.speech_style,
                is_dialogue=any(
                    getattr(s, "is_dialogue", False) for s in unit.sentences
                ),
            )

            best = select_best(variants)
            if best:
                unit.selected_variant_id = best.variant_id
                unit.translated_text = best.text
                unit.predicted_duration_ms = best.predicted_duration_ms
                unit.prediction_confidence = best.prediction_confidence
                unit.meaning_score = best.meaning_score
                unit.naturalness_score = best.naturalness_score
                unit.dialogue_score = best.dialogue_score
                unit.duration_score = best.duration_score
                unit.emotion_score = best.emotion_score
                unit.prosody_score = best.prosody_score
                unit.lipsync_readiness = best.lipsync_readiness
                unit.runtime_cost = best.runtime_cost

            unit.adaptation_variants = [v.to_dict() for v in variants]
            # region agent log
            _debug_log(
                "H2,H3",
                "meaning_first_pipeline.py:211",
                "Variant duration and selection evidence",
                {
                    "unitSlotMs": unit.slot_ms,
                    "selectedLabel": best.label if best else "",
                    "selectedStrategy": best.strategy if best else "",
                    "variants": [
                        {
                            "label": variant.label,
                            "strategy": variant.strategy,
                            "textLength": len(variant.text),
                            "predictedMs": variant.predicted_duration_ms,
                            "durationScore": round(variant.duration_score, 2),
                            "compositeScore": round(variant.composite_score(), 2),
                            "rejected": variant.rejected,
                        }
                        for variant in variants
                    ],
                },
            )
            # endregion

    logger.info("P106-P108 Semantic Adaptation + Strategy Selection: done")

    # ── P117: Meaning Preservation validation ───────────────────────────
    from engines.semantic_v3.stage_validator import validate_meaning_preservation

    for unit in meaning_units:
        mp = validate_meaning_preservation(
            unit.text,
            unit.translated_text,
            entities=[e for s in unit.sentences for e in getattr(s, "entities", [])],
        )
        unit.validation_status = "passed" if mp.passed else "failed"
        unit.validation_errors = list(mp.errors)

    # ── P119 gate: translation → translation_lock ───────────────────────
    _validate_gate(
        "semantic_adaptation", "translation_lock",
        meaning_units=meaning_units,
    )

    # ── P109: Translation LOCK ──────────────────────────────────────────
    if lock:
        from engines.semantic_v3.meaning_lock import lock_all_meaning_units

        # region agent log
        _debug_log(
            "H4",
            "meaning_first_pipeline.py:259",
            "Translation lock prerequisites",
            {
                "forced": True,
                "units": [
                    {
                        "hasSelectedVariant": bool(unit.selected_variant_id),
                        "validationStatus": unit.validation_status,
                        "hasPredictedDuration": unit.predicted_duration_ms > 0,
                    }
                    for unit in meaning_units
                ],
            },
        )
        # endregion
        meaning_units = lock_all_meaning_units(meaning_units, force=True)
        logger.info("P109 Translation LOCK: all %d units locked", len(meaning_units))

    # ── P110: Speech Planning ───────────────────────────────────────────
    from engines.semantic_v3.speech_planning import build_speech_plans

    speech_plans = build_speech_plans(meaning_units, lang=tgt_lang)

    logger.info("P110 Speech Planning: %d plans, %d with overflow risk",
                len(speech_plans),
                sum(1 for p in speech_plans if p.overflow_probability > 0.5))
    # region agent log
    _debug_log(
        "H1,H3",
        "meaning_first_pipeline.py:286",
        "Speech plan fit after variant selection",
        {
            "plans": [
                {
                    "slotMs": plan.slot_ms,
                    "expectedDurationMs": plan.expected_duration_ms,
                    "tempo": plan.expected_tempo,
                    "overflowProbability": round(plan.overflow_probability, 3),
                    "underflowProbability": round(plan.underflow_probability, 3),
                }
                for plan in speech_plans
            ],
        },
    )
    # endregion

    # ── P119 gate: speech_planning → tts ────────────────────────────────
    _validate_gate(
        "speech_planning", "tts",
        meaning_units=meaning_units,
    )

    # ── Build SemanticProject with MeaningUnits ─────────────────────────
    # Sync sentences back from meaning units
    all_sentences = []
    for mu in meaning_units:
        for sent in mu.sentences:
            if not sent.translated_text and mu.translated_text:
                sent.translated_text = mu.translated_text
            all_sentences.append(sent)

    project = SemanticProject(
        words=words,
        sentences=all_sentences,
        meaning_units=meaning_units,
        asr_archive=archive,
        unit_type="meaning_unit",
        phase="V2",
        meta={
            "pipeline": "meaning_first_v2",
            "whisper_owner": False,
            "word_count": len(words),
            "sentence_count": len(all_sentences),
            "meaning_unit_count": len(meaning_units),
            "speech_plans": [p.to_dict() for p in speech_plans],
            "p116_violations": segment_violations,
            "original_word_count": original_word_count,
        },
    )

    result = {
        "project": project,
        "words": words,
        "sentences": all_sentences,
        "meaning_units": meaning_units,
        "speech_plans": speech_plans,
        "archive": archive,
        "meta": project.meta,
    }

    logger.info(
        "MeaningFirstPipeline V2: archive=%d words=%d sentences=%d units=%d plans=%d",
        len(archive), len(words), len(all_sentences),
        len(meaning_units), len(speech_plans),
    )
    return result


def _build_asr_archive(
    asr_texts: list[str], asr_timing: list[Any]
) -> list[dict[str, Any]]:
    archive = []
    for i, text in enumerate(asr_texts):
        row: dict[str, Any] = {
            "index": i,
            "text": text,
            "unit_type": "asr_archive_only",
        }
        if i < len(asr_timing) and isinstance(asr_timing[i], dict):
            row["start"] = asr_timing[i].get("start")
            row["end"] = asr_timing[i].get("end")
        archive.append(row)
    return archive


def _translate_meaning_units(
    units: list[Any],
    *,
    src_lang: str,
    tgt_lang: str,
    translate_fn: Callable[[str, str, str], str] | None = None,
    app_dir: Any = None,
) -> list[Any]:
    """P105: translate each MeaningUnit (not Whisper segment)."""
    from engines.semantic_v3.native_translate import translate_sentences_native

    for unit in units:
        translated = translate_sentences_native(
            unit.sentences,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            translate_fn=translate_fn,
            app_dir=app_dir,
            lock=False,
        )
        unit.sentences = translated
        combined = " ".join(
            s.translated_text for s in translated if s.translated_text
        )
        if combined.strip():
            unit.translated_text = combined.strip()

    return units


def _validate_gate(
    stage_from: str,
    stage_to: str,
    **kwargs: Any,
) -> None:
    """P119: run stage transition validation."""
    try:
        from engines.semantic_v3.stage_validator import validate_stage_transition

        validate_stage_transition(
            stage_from, stage_to,
            raise_on_fail=False,
            **kwargs,
        )
    except Exception as exc:
        logger.warning("P119 gate %s→%s warning: %s", stage_from, stage_to, exc)
