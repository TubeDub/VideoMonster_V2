"""Semantic V3 Phase 2 — Native Meaning Pipeline (P31–P50 + Part 2 Semantic Core)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.absolute_rules import (
    apply_overflow_state,
    apply_underflow_state,
    assert_no_overlap_slots,
    assert_no_sentence_split_across_segments,
    assert_no_tail_spill,
)
from engines.semantic_v3.adaptive_planning import try_rewrite_v2
from engines.semantic_v3.adjacent_scene_check import (
    revalidate_neighbors_or_revert,
    snapshot_neighbors,
    snapshot_sentence_state,
)
from engines.semantic_v3.duration_predictor import apply_duration_predictor
from engines.semantic_v3.native_translate import translate_sentences_native
from engines.semantic_v3.quality import review_payload, validate_all
from engines.semantic_v3.quality_planner import plan_quality
from engines.semantic_v3.regression_wall import enforce_regression_wall
from engines.semantic_v3.semantic_core import run_semantic_core
from engines.semantic_v3.semantic_lock import lock_all
from engines.semantic_v3.sentence_builder import assert_sentences_atomic
from engines.semantic_v3.time_equivalence import (
    evaluate_and_mark,
    mark_readaptation_pass,
)
from engines.semantic_v3.types import SemanticProject
from engines.semantic_v3.word_alignment import align_words_to_sentences, word_alignment_report

logger = logging.getLogger("tubedub.semantic_v3.phase2")
_DEBUG_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-7e57dc.log"


def _debug_log(
    hypothesis_id: str, location: str, message: str, data: dict[str, Any]
) -> None:
    """Opt-in NDJSON diagnostics (VM_DEBUG_NDJSON=1). Off in production."""
    import os

    if (os.getenv("VM_DEBUG_NDJSON") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    try:
        payload = {
            "sessionId": "7e57dc",
            "runId": "meaning-fit-production-path",
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


def _launch_trace(
    stage: str,
    *,
    status: str,
    reason: str,
    line: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Emit a launch-decision-trace stage record from phase2.

    ``run_semantic_v3_phase2`` does not receive the AutoDub task dict;
    we still emit to the shared NDJSON debug log so studio/OpenDDF can
    reconstruct the full stage ledger. Auto_dub_api owns the copy that
    lands inside ``task['info']['launch_decision_trace']``.
    """
    try:
        from engines.semantic_v3.launch_decision_trace import record_stage

        record_stage(
            stage,
            status=status,
            reason=reason,
            module="engines/semantic_v3/phase2.py",
            line=line,
            data=data or {},
            task_info=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("phase2 launch trace skipped for stage=%s: %s", stage, exc)


def _launch_trace_agent(
    agent: str,
    *,
    called: bool,
    called_by: str = "",
    skipped_reason: str = "",
    line: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Emit a launch-decision-trace AI-agent record from phase2."""
    try:
        from engines.semantic_v3.launch_decision_trace import record_agent

        record_agent(
            agent,
            called=called,
            called_by=called_by,
            skipped_reason=skipped_reason,
            module="engines/semantic_v3/phase2.py",
            line=line,
            data=data or {},
            task_info=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("phase2 launch trace agent skipped for %s: %s", agent, exc)


def run_semantic_v3_phase2(
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
) -> SemanticProject:
    """
    Native Meaning Pipeline:
    ASR archive → Semantic Core → (Translate) → Lock → Align → Duration →
    Plan → SpeechUnits → Scheduler AudioUnits.
    """
    # region agent log
    _debug_log(
        "H5",
        "phase2.py:70",
        "Production semantic pipeline entered",
        {
            "asrInputCount": len(asr_texts),
            "timingInputCount": len(asr_timing),
            "translationEnabled": translate,
            "targetLanguage": tgt_lang,
            "voiceConfigured": voice != "default",
        },
    )
    # endregion
    if not asr_texts:
        _launch_trace(
            "Meaning Pipeline",
            status="FAILED",
            reason="zero_asr_inputs",
            line=128,
            data={"asrInputCount": 0},
        )
        raise ArchitectureViolation(
            "Semantic V3 Phase2 received zero ASR inputs",
            stage="meaning_pipeline",
            rule="asr_input_count_min_1",
        )
    _launch_trace(
        "Meaning Pipeline",
        status="SUCCESS",
        reason="phase2_entered",
        line=128,
        data={"asrInputCount": len(asr_texts)},
    )
    archive = []
    for i, text in enumerate(asr_texts):
        row: dict[str, Any] = {"index": i, "text": text, "unit_type": "asr_archive_only"}
        if i < len(asr_timing):
            tm = asr_timing[i]
            if isinstance(tm, dict):
                row["start"] = tm.get("start")
                row["end"] = tm.get("end")
        archive.append(row)

    # Part 2 Semantic Core (P101–P120) — Whisper is not decision owner
    core = run_semantic_core(
        asr_texts,
        asr_timing,
        word_maps=word_maps,
        src_lang=src_lang,
        content_mode=content_mode,
        place=place,
    )
    words = core.words
    sentences = core.sentences
    assert_sentences_atomic(sentences)
    core_meta = dict(core.meta or {})
    _launch_trace(
        "Sentence Builder",
        status="SUCCESS",
        reason="semantic_core_ok",
        line=155,
        data={"words": len(words), "sentences": len(sentences)},
    )
    _launch_trace_agent(
        "semantic",
        called=True,
        called_by="engines/semantic_v3/semantic_core.py:run_semantic_core",
        line=155,
        data={"sentences": len(sentences)},
    )
    _launch_trace_agent(
        "entity",
        called=True,
        called_by="engines/semantic_v3/semantic_core.py:run_semantic_core",
        line=155,
        data={"entity_slots": sum(len(s.entities or []) for s in sentences)},
    )

    # P31/P33 native translation
    if translate:
        sentences = translate_sentences_native(
            sentences,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            translate_fn=translate_fn,
            app_dir=app_dir,
            lock=False,
        )
        _launch_trace(
            "Translation",
            status="SUCCESS",
            reason="native_translate_ok",
            line=168,
            data={
                "sentences": len(sentences),
                "src": src_lang,
                "tgt": tgt_lang,
            },
        )
        _launch_trace_agent(
            "translation",
            called=True,
            called_by=(
                "engines/semantic_v3/native_translate.py:"
                "translate_sentences_native"
            ),
            line=168,
            data={"sentences": len(sentences)},
        )
    else:
        _launch_trace(
            "Translation",
            status="SKIPPED",
            reason="translate_flag_off_caller_supplied_target_text",
            line=168,
            data={"sentences": len(sentences)},
        )
        _launch_trace_agent(
            "translation",
            called=False,
            skipped_reason="translate_disabled_phase2_caller_supplied_text",
            line=168,
        )
    # region agent log
    _debug_log(
        "H4",
        "phase2.py:105",
        "Translation lock state immediately after translation",
        {
            "sentenceCount": len(sentences),
            "lockedCount": sum(
                1 for sentence in sentences if sentence.semantic_locked
            ),
            "translatedCount": sum(
                1 for sentence in sentences if bool(sentence.translated_text)
            ),
        },
    )
    # endregion

    # P35–P37 word alignment + articulation
    sentences = align_words_to_sentences(
        sentences, language=src_lang, speech_rate=1.0
    )
    align_report = word_alignment_report(sentences)

    # Snapshot of the pre-Meaning-Fit projection (used by the Regression Wall
    # to detect disappearance / relocation / duplication against the source).
    # We deep-copy via to_dict/from_dict because Meaning Fit will mutate
    # sentence.text / sentence.end_ms in-place for multi-sentence units.
    from engines.semantic_v3.types import SemanticSentence as _SemSent

    _original_projection = [
        _SemSent.from_dict(s.to_dict()) for s in sentences
    ]
    # Meaning Fit V2: MeaningUnit → Target Duration → best natural expression.
    # This is the sole pre-LOCK text-fitting stage.
    from engines.semantic_v3.meaning_fit_engine import fit_meaning_units_to_target

    sentences = fit_meaning_units_to_target(
        sentences,
        voice=voice,
        tgt_lang=tgt_lang,
    )
    # Aggregate Meaning Preservation V2 trace (coverage / entities / events)
    _mp_agg = {
        "blocks_original": 0,
        "blocks_preserved": 0,
        "entities_original": 0,
        "entities_preserved": 0,
        "events_original": 0,
        "events_preserved": 0,
        "fallback_count": 0,
        "narrative_fail": 0,
        "sentence_fail": 0,
        "coverage_sum": 0.0,
        "n": 0,
    }
    for _s in sentences:
        _mp = getattr(_s, "meaning_preservation", None) or {}
        if not isinstance(_mp, dict):
            continue
        _mp_agg["n"] += 1
        _mb = _mp.get("Meaning blocks") or {}
        _ent = _mp.get("Entities") or {}
        _ev = _mp.get("Events") or {}
        _mp_agg["blocks_original"] += int(_mb.get("Original") or 0)
        _mp_agg["blocks_preserved"] += int(_mb.get("Preserved") or 0)
        _mp_agg["entities_original"] += int(_ent.get("Original") or 0)
        _mp_agg["entities_preserved"] += int(_ent.get("Preserved") or 0)
        _mp_agg["events_original"] += int(_ev.get("Original") or 0)
        _mp_agg["events_preserved"] += int(_ev.get("Preserved") or 0)
        if str(_mp.get("Fallback") or "").upper() == "YES":
            _mp_agg["fallback_count"] += 1
        if str(_mp.get("Narrative") or "").upper() == "FAILED":
            _mp_agg["narrative_fail"] += 1
        if str(_mp.get("Sentence integrity") or "").upper() == "FAILED":
            _mp_agg["sentence_fail"] += 1
        _cov = str(_mp.get("Coverage") or "0%").replace("%", "")
        try:
            _mp_agg["coverage_sum"] += float(_cov) / 100.0
        except ValueError:
            pass
    _mp_coverage = (
        round(_mp_agg["coverage_sum"] / max(1, _mp_agg["n"]), 3)
        if _mp_agg["n"]
        else 1.0
    )
    _launch_trace(
        "Meaning Preservation",
        status="SUCCESS" if _mp_agg["narrative_fail"] == 0 else "WARN",
        reason="meaning_coverage_gate",
        line=306,
        data={
            "Meaning blocks": {
                "Original": _mp_agg["blocks_original"],
                "Preserved": _mp_agg["blocks_preserved"],
            },
            "Entities": {
                "Original": _mp_agg["entities_original"],
                "Preserved": _mp_agg["entities_preserved"],
            },
            "Events": {
                "Original": _mp_agg["events_original"],
                "Preserved": _mp_agg["events_preserved"],
            },
            "Narrative": "PASSED" if _mp_agg["narrative_fail"] == 0 else "FAILED",
            "Sentence integrity": (
                "PASSED" if _mp_agg["sentence_fail"] == 0 else "FAILED"
            ),
            "Coverage": f"{round(_mp_coverage * 100)}%",
            "Fallback": "YES" if _mp_agg["fallback_count"] else "NO",
            "fallback_count": _mp_agg["fallback_count"],
            "units": _mp_agg["n"],
        },
    )
    _launch_trace(
        "Variant Generator",
        status="SUCCESS",
        reason="meaning_fit_variants_scored",
        line=214,
        data={"sentences": len(sentences)},
    )
    # region agent log
    _debug_log(
        "H13,H14,H15",
        "phase2.py:151",
        "Meaning Fit candidate selection details",
        {
            "sentences": [
                {
                    "targetMs": getattr(sentence, "target_duration", {}).get(
                        "target_ms", 0
                    ),
                    "selectedVariantId": getattr(
                        sentence, "selected_variant_id", ""
                    ),
                    "meaningPreservation": getattr(
                        sentence, "meaning_preservation", None
                    ),
                    "meaningFallback": bool(
                        getattr(sentence, "meaning_preservation_fallback", False)
                    ),
                    "variants": [
                        {
                            "label": variant.get("label"),
                            "strategy": variant.get("strategy"),
                            "predictedMs": variant.get("predicted_duration_ms"),
                            "durationScore": variant.get("duration_score"),
                            "compositeScore": variant.get("composite_score"),
                            "selected": variant.get("selected"),
                        }
                        for variant in getattr(
                            sentence, "adaptation_variants", []
                        )
                    ],
                }
                for sentence in sentences
            ],
        },
    )
    # endregion

    # P38 duration predictor (phoneme-based)
    sentences = apply_duration_predictor(sentences, voice=voice, tgt_lang=tgt_lang)
    for s in sentences:
        apply_overflow_state(s)
        apply_underflow_state(s)
    _launch_trace(
        "Duration Predictor",
        status="SUCCESS",
        reason="phoneme_predictor_applied",
        line=252,
        data={"sentences": len(sentences)},
    )
    _launch_trace_agent(
        "timing",
        called=True,
        called_by="engines/semantic_v3/duration_predictor.py:apply_duration_predictor",
        line=252,
        data={"sentences": len(sentences)},
    )
    # region agent log
    _debug_log(
        "H1,H3",
        "phase2.py:126",
        "Production duration prediction before decision policy",
        {
            "sentences": [
                {
                    "slotMs": sentence.slot_ms,
                    "predictedMs": sentence.predicted_tts_ms,
                    "overflowMs": sentence.overflow_ms,
                    "underflowMs": sentence.underflow_ms,
                    "locked": sentence.semantic_locked,
                }
                for sentence in sentences
            ],
        },
    )
    # endregion

    # Part 4 — Decision Policy Engine (strategy only; no text/audio mutation)
    from engines.decision_policy import run_decision_policy

    decision_graph = run_decision_policy(
        sentences,
        profile=content_mode or "",
        attach=True,
    )

    # P41 dynamic merge — execute only when strategy selected merge
    from engines.semantic_v3.adaptive_planning import dynamic_sentence_merge

    merge_needed = any(
        r.accepted and "sentence_merge" in (r.accepted.steps or [])
        for r in decision_graph.records
    )
    if merge_needed:
        sentences = dynamic_sentence_merge(sentences)
    # P43 rewrite only before lock (Decision Policy forbids post-lock rewrite)
    _before_rewrite_texts = [
        sentence.translated_text or sentence.text for sentence in sentences
    ]
    rewritten: list = []
    for s in sentences:
        if s.semantic_locked or getattr(s, "meaning_fit_selected", False):
            rewritten.append(s)
        else:
            rewritten.append(try_rewrite_v2(s))
    sentences = rewritten
    # region agent log
    _debug_log(
        "H2,H4",
        "phase2.py:164",
        "Rewrite eligibility after decision policy",
        {
            "totalSentences": len(sentences),
            "rewritableCount": sum(
                1 for sentence in sentences if not sentence.semantic_locked
            ),
            "lockedCount": sum(
                1 for sentence in sentences if sentence.semantic_locked
            ),
        },
    )
    # endregion
    # region agent log
    _debug_log(
        "H10,H11,H12",
        "phase2.py:184",
        "Symmetric meaning-fit rewrite outcome",
        {
            "sentences": [
                {
                    "underflowMs": sentence.underflow_ms,
                    "overflowMs": sentence.overflow_ms,
                    "textChanged": (
                        (sentence.translated_text or sentence.text)
                        != _before_rewrite_texts[index]
                    ),
                    "lengthBefore": len(_before_rewrite_texts[index]),
                    "lengthAfter": len(sentence.translated_text or sentence.text),
                    "voiceAwarePrediction": bool(
                        getattr(sentence, "duration_prediction", {}).get("method")
                        == "phoneme_voice_profile"
                    ),
                }
                for index, sentence in enumerate(sentences)
            ],
        },
    )
    # endregion
    # Re-predict + re-decide after structural changes
    sentences = apply_duration_predictor(sentences, voice=voice, tgt_lang=tgt_lang)
    decision_graph = run_decision_policy(
        sentences,
        profile=content_mode or decision_graph.profile,
        attach=True,
    )

    # ── ЭТАП 7 — Time Equivalence Check ────────────────────────────────
    # Compare original slot cadence against the adapted (predicted) TTS
    # duration. Anything outside tolerance is marked needs_readaptation
    # so Meaning Fit can run exactly one extra pass on that subset.
    time_eq_report = evaluate_and_mark(sentences)
    # region agent log
    lock_count_before_readaptation = sum(
        1 for sentence in sentences if sentence.semantic_locked
    )
    _debug_log(
        "AR-ET7",
        "phase2.py:time_equivalence",
        "Time equivalence evaluated before any re-adaptation",
        {
            "flaggedCount": len(time_eq_report.flagged),
            "toleranceHalf": time_eq_report.tolerance_pct,
            "lockedBefore": lock_count_before_readaptation,
            "targetDurationUsedEverywhere": all(
                isinstance(getattr(sentence, "target_duration", None), dict)
                for sentence in sentences
            ),
        },
    )
    # endregion

    # ── ЭТАП 9 gated re-adaptation (single extra Meaning Fit pass) ─────
    if time_eq_report.flagged:
        flagged_uuids = {row.sentence_uuid for row in time_eq_report.flagged}
        adjacent_reports: list[dict[str, Any]] = []
        for index, sent in enumerate(list(sentences)):
            if sent.sentence_uuid not in flagged_uuids:
                continue
            if sent.semantic_locked:
                # LOCK must be strictly after re-adaptation — refuse to
                # touch anything already locked.
                raise ArchitectureViolation(
                    "ЭТАП 7: locked sentence marked for re-adaptation",
                    stage="time_equivalence",
                    rule="lock_before_adaptation",
                    segment_id=sent.sentence_uuid,
                )
            prev_snap, next_snap, budget_before = snapshot_neighbors(
                sentences, index
            )
            snap_state = snapshot_sentence_state(sent)
            # Wrap the offending sentence in a synthetic MeaningUnit and
            # re-run the Meaning Fit engine ONCE.
            from engines.semantic_v3.meaning_fit_engine import (
                fit_meaning_units_to_target,
            )

            sentences[index : index + 1] = fit_meaning_units_to_target(
                [sent], voice=voice, tgt_lang=tgt_lang
            )
            apply_duration_predictor(
                [sentences[index]], voice=voice, tgt_lang=tgt_lang
            )
            report = revalidate_neighbors_or_revert(
                sentences,
                changed_index=index,
                original_state=snap_state,
                prev_snapshot=prev_snap,
                next_snapshot=next_snap,
                scene_budget_before_ms=budget_before,
            )
            adjacent_reports.append(report.to_dict())
        # One extra pass has now been consumed for every flagged unit.
        mark_readaptation_pass(sentences)
        evaluate_and_mark(sentences)  # refreshes time_equivalence metadata; no third pass permitted
        # region agent log
        _debug_log(
            "AR-ET9",
            "phase2.py:adjacent_scene_check",
            "Adjacent scene revalidation completed after ЭТАП 7 re-adaptation",
            {
                "reAdaptedCount": len(adjacent_reports),
                "revertedCount": sum(
                    1 for r in adjacent_reports if r.get("reverted")
                ),
                "reasons": [r.get("reason") for r in adjacent_reports],
            },
        )
        # endregion
    else:
        _debug_log(
            "AR-ET9",
            "phase2.py:adjacent_scene_check",
            "Adjacent scene revalidation skipped — no ЭТАП 7 flags",
            {"reAdaptedCount": 0, "revertedCount": 0},
        )

    # ── ЭТАП 10 — Regression Wall ──────────────────────────────────────
    # Hard boundary between Meaning Fit and LOCK. Refuses to promote a
    # project past LOCK when any forbidden outcome is detected.
    wall_report = enforce_regression_wall(
        _original_projection,
        sentences,
        hard_fail=True,
    )
    # region agent log
    _debug_log(
        "AR-ET10",
        "phase2.py:regression_wall",
        "Regression Wall verdict before LOCK",
        {
            "passed": wall_report.passed,
            "checksRun": wall_report.checks_run,
            "checksPassed": wall_report.checks_passed,
            "slotCount": wall_report.slot_count,
            "lockCountBeforeAdaptation": lock_count_before_readaptation,
            "forbiddenOutcomeDetected": bool(wall_report.violations),
        },
    )
    # endregion

    _launch_trace(
        "Meaning Fit",
        status="SUCCESS",
        reason="rewrite_and_regression_wall_ok",
        line=458,
        data={
            "regression_wall_passed": bool(wall_report.passed),
            "flagged_before_readapt": len(time_eq_report.flagged),
        },
    )
    _launch_trace(
        "Adaptation",
        status="SUCCESS",
        reason="adjacent_scene_revalidation_complete",
        line=462,
        data={"sentences": len(sentences)},
    )

    # Semantic LOCK is deliberately last: translation, duration prediction,
    # structural planning and allowed text rewrites must all finish first.
    sentences = lock_all(sentences)
    # region agent log
    _debug_log(
        "AR-LOCK",
        "phase2.py:lock_all",
        "Semantic LOCK applied after all gates passed",
        {
            "sentenceCount": len(sentences),
            "lockedAfter": sum(1 for s in sentences if s.semantic_locked),
            "lockOrderCorrect": True,
        },
    )
    # endregion

    # Part 5 — Dub Engine 2.0 (post-lock audio only)
    from engines.dub_engine_v2 import run_dub_engine

    dub = run_dub_engine(
        sentences,
        voice=voice,
        profile=content_mode or decision_graph.profile,
        hard_fail_overlap=True,
        require_wav_files=False,
    )
    speech = dub.speech_units
    timeline = dub.timeline
    _launch_trace(
        "Timeline",
        status="SUCCESS",
        reason="dub_engine_timeline_built",
        line=541,
        data={"units": len(timeline.units)},
    )
    _launch_trace(
        "Scheduler",
        status="SUCCESS",
        reason="dub_engine_speech_units_scheduled",
        line=541,
        data={"speech_units": len(speech)},
    )
    _launch_trace(
        "TTS",
        status="SKIPPED",
        reason="phase2_planning_only_require_wav_files_false",
        line=538,
        data={"require_wav_files": False},
    )
    _launch_trace_agent(
        "mix",
        called=False,
        skipped_reason="mix_deferred_to_autodub_render_stage",
        line=538,
    )

    from engines.semantic_v3.adaptation import assign_dub_segments

    sentences = assign_dub_segments(sentences)
    assert_no_sentence_split_across_segments(sentences)
    assert_no_tail_spill(sentences)
    assert_no_overlap_slots(sentences)

    qplans = plan_quality(sentences)
    qa = validate_all(sentences)
    _launch_trace_agent(
        "grammar",
        called=True,
        called_by="engines/semantic_v3/quality_planner.py:plan_quality",
        line=550,
        data={"plans": len(qplans)},
    )
    _launch_trace_agent(
        "quality",
        called=True,
        called_by="engines/semantic_v3/quality.py:validate_all",
        line=551,
        data={"validation_ok": bool(qa)},
    )

    meta: dict = {
        "phase2": True,
        "semantic_core": True,
        "dub_engine_v2": True,
        "bridge": False,
        "whisper_owner": False,
        "anti_regression": {
            "time_equivalence": time_eq_report.to_dict(),
            "regression_wall": wall_report.to_dict(),
        },
        "core": core_meta,
        "word_alignment": align_report,
        "decision_graph": decision_graph.to_dict(),
        "dub": dub.to_dict(),
        "adaptive_plans": [
            getattr(s, "adaptive_plan").to_dict()
            for s in sentences
            if getattr(s, "adaptive_plan", None) is not None
        ],
        "quality_plans": [q.to_dict() for q in qplans],
        "validation": qa,
        "review": review_payload(sentences),
        "speech_units": [u.to_dict() for u in speech],
        "timeline": timeline.to_dict(),
        "audio_metrics": dub.metrics.to_dict(),
        "tts_blocked": sum(
            1
            for s in sentences
            if not getattr(getattr(s, "adaptive_plan", None), "tts_allowed", True)
        ),
    }
    # Part 6 — Studio 2.0 / Diagnostics / QA bundle (observability surface)
    try:
        from engines.studio_qa import build_studio_qa_bundle

        studio_bundle = build_studio_qa_bundle(
            sentences=sentences,
            meta=meta,
            info={"pipeline_state": "SPEECH_READY"},
            pipeline_state="SPEECH_READY",
        )
        meta["studio_qa"] = studio_bundle.to_dict()
    except Exception as exc:
        logger.warning("StudioQA bundle failed: %s", exc)
        meta["studio_qa"] = {"version": "6.0", "error": str(exc)}

    # Part 7 — Voice Platform plans + Lip Sync 2.0 data (no provider coupling in Dub Engine)
    try:
        from engines.voice_platform import run_voice_platform_for_meta

        meta["voice_platform"] = run_voice_platform_for_meta(
            meta,
            voice=voice if voice and voice != "default" else None,
            language=tgt_lang or "ru",
            style=content_mode or (
                str(getattr(sentences[0], "style", None) or "Movie") if sentences else "Movie"
            ),
        )
        _launch_trace_agent(
            "voice",
            called=True,
            called_by="engines/voice_platform/__init__.py:run_voice_platform_for_meta",
            line=603,
        )
    except Exception as exc:
        logger.warning("VoicePlatform plan failed: %s", exc)
        meta["voice_platform"] = {"version": "7.0", "error": str(exc)}
        _launch_trace_agent(
            "voice",
            called=False,
            skipped_reason=f"voice_platform_exception:{type(exc).__name__}",
            line=611,
        )

    project = SemanticProject(
        words=words,
        sentences=sentences,
        asr_archive=archive,
        unit_type="speech_unit",
        phase="P50",
        meta=meta,
    )
    logger.info(
        "SemanticV3 Phase2+Core: archive=%d words=%d sentences=%d speech=%d audio=%d",
        len(archive),
        len(words),
        len(sentences),
        len(speech),
        len(timeline.units),
    )
    return project


def phase2_to_orchestrator_arrays(
    project: SemanticProject,
) -> tuple[list[str], list[dict[str, int]], list[str]]:
    """
    Export for legacy AutoDub TTS loop — derived from Speech/Audio Units only.
    NOT a Whisper bridge: texts are SpeechUnit texts; timing from Timeline.
    """
    speech = project.meta.get("speech_units") or []
    timeline = (project.meta.get("timeline") or {}).get("units") or []
    if speech and timeline and len(speech) == len(timeline):
        texts = [str(s.get("text") or "") for s in speech]
        timing = [
            {"start": int(u.get("start_ms") or 0), "end": int(u.get("end_ms") or 0)}
            for u in timeline
        ]
        sources = [str(s.get("source_text") or "") for s in speech]
        return sources, timing, texts
    sources = [s.text for s in project.sentences]
    timing = [{"start": s.start_ms, "end": s.end_ms} for s in project.sentences]
    texts = [s.translated_text or s.text for s in project.sentences]
    return sources, timing, texts
