"""TubeDub Semantic Agent v1.0 — natural rewrite preserving meaning."""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.semantic_agent.candidate_selector import (
    generate_candidates,
    select_best_candidate,
)
from engines.ai_core.semantic_agent.retry_policy import (
    MEANING_THRESHOLD,
    apply_retry_policy,
)
from engines.ai_core.semantic_agent.scoring import SegmentScores, aggregate_averages
from engines.ai_core.semantic_agent.validators.context_validator import validate_context
from engines.ai_core.semantic_agent.validators.emotion_validator import validate_emotion
from engines.ai_core.semantic_agent.validators.meaning_validator import validate_meaning
from engines.ai_core.entity_dictionary import EntityDictionary
from engines.ai_core.semantic_engine.context_bundle import DialogueContext, build_dialogue_context
from engines.ai_core.semantic_engine.quality_audit import (
    MAX_SEMANTIC_RETRIES,
    SEMANTIC_SCORE_MIN,
    audit_semantic_output,
)
from engines.ai_core.semantic_engine.quality_report import write_semantic_quality_report
from engines.ai_core.translation_agent.agent import load_manifest
from engines.mt.lang_codes import normalize_lang
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

logger = logging.getLogger("tubedub.ai_core.semantic_agent")

_APP_DIR = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _APP_DIR / "output"
_MANIFESTS_DIR = _OUTPUT_DIR / "manifests"


def _brief_semantic_threshold(seg: dict[str, Any], default: float) -> float:
    brief = seg.get("creative_brief") or {}
    if not brief:
        return default
    adaptation = float(brief.get("adaptation_priority", 0.7))
    deep = bool(brief.get("deep_semantic_adaptation_needed"))
    threshold = default - (adaptation - 0.7) * 0.1
    if deep:
        threshold -= 0.05
    return max(0.5, min(0.99, threshold))


class SemanticAgent:
    """Context-aware semantic adaptation v4.0 — writes segments[].semantic_text only."""

    VERSION = "4.0"

    def __init__(
        self,
        output_dir: Path | None = None,
        *,
        meaning_threshold: float = MEANING_THRESHOLD,
    ):
        self.output_dir = output_dir or _OUTPUT_DIR
        self._manifests_dir = self.output_dir / "manifests"
        self.meaning_threshold = meaning_threshold

    def _check_gatekeeper(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        from engines.ai_core.gatekeeper_peer import check_upstream_gate

        ok, errors, warnings = check_upstream_gate("semantic", manifest, state)
        return ok, errors + warnings

    def _source_target(self, manifest: dict[str, Any]) -> tuple[str, str]:
        src = normalize_lang(manifest.get("source_lang") or "en")
        tgt = normalize_lang(manifest.get("target_lang") or "ru")
        return src, tgt

    def _pass1_meaning_analysis(self, source: str, translated: str) -> dict[str, Any]:
        """Extract key facts/entities from source + translation."""
        from engines.ai_core.translation_agent.validators.entity_validator import (
            extract_entities,
        )

        return {
            "source_entities": extract_entities(source),
            "translated_entities": extract_entities(translated),
            "source_len": len(source),
            "translated_len": len(translated),
        }

    def _process_segment(
        self,
        seg: dict[str, Any],
        *,
        src_lang: str,
        tgt_lang: str,
        dialogue: DialogueContext,
        entity_dict: EntityDictionary,
        stats: dict[str, Any],
        decision_log: list[str],
        app_dir: Path | None = None,
        force_llm: bool = False,
    ) -> dict[str, Any]:
        source = str(seg.get("text") or dialogue.source or "").strip()
        translated = str(seg.get("translated_text") or dialogue.translated or "").strip()
        idx = seg.get("index", dialogue.index)
        prev_context = dialogue.prev_context_text() or None

        if not translated:
            decision_log.append(f"segment_{idx}:missing_translation_peer_contract")
            seg["semantic_text"] = ""
            return {
                "index": idx,
                "semantic_text": "",
                "selected_variant": "peer_contract_skip",
                "scores": SegmentScores(0.0, 0.0, 0.0, 0.0, 0.0).to_dict(),
                "candidates": [],
                "decision_log": ["missing_translation_peer_contract"],
            }

        analysis = self._pass1_meaning_analysis(source, translated)
        decision_log.append(
            f"segment_{idx}:pass1 entities={len(analysis['source_entities'])}"
        )

        brief = seg.get("creative_brief") or {}
        use_llm = True
        if brief and brief.get("deep_semantic_adaptation_needed") is False and not force_llm:
            use_llm = False
            decision_log.append(f"segment_{idx}:brief_shallow_semantic")
        if force_llm:
            use_llm = True
            decision_log.append(f"segment_{idx}:quality_retry_force_llm")

        dialogue_block = dialogue.prompt_block()
        variants, llm_used = generate_candidates(
            source,
            translated,
            tgt_lang=tgt_lang,
            prev_context=prev_context,
            dialogue_block=dialogue_block,
            use_llm=use_llm,
            app_dir=app_dir,
        )
        stats["variants_generated"] += len(variants)
        if llm_used:
            stats["llm_used"] = True
        stats["rule_rewrite_used"] = True

        selection = select_best_candidate(
            source,
            translated,
            variants,
            tgt_lang=tgt_lang,
            prev_context=prev_context,
            llm_used=llm_used,
        )

        candidate_text = selection.best.text
        meaning = validate_meaning(source, translated, candidate_text)

        seg_threshold = _brief_semantic_threshold(seg, self.meaning_threshold)
        if meaning.score < seg_threshold:
            retry = apply_retry_policy(
                source,
                translated,
                candidate_text,
                meaning.score,
                tgt_lang=tgt_lang,
                prev_context=prev_context,
            )
            decision_log.extend([f"segment_{idx}:{d}" for d in retry.decision_log])
            candidate_text = retry.text
            meaning = validate_meaning(source, translated, candidate_text)
            if retry.used_fallback:
                stats["warnings"].append(
                    f"segment_{idx}:meaning_fallback translated_text used"
                )

        context = validate_context(
            source, translated, candidate_text, prev_context=prev_context
        )
        emotion = validate_emotion(source, translated, candidate_text)

        if not context.ok:
            stats["warnings"].append(f"segment_{idx}:context_score={context.score:.3f}")
        if not emotion.ok:
            stats["warnings"].append(
                f"segment_{idx}:emotion_mismatch {emotion.source_emotion}->{emotion.candidate_emotion}"
            )

        semantic_text = str(candidate_text or "").strip() or translated
        semantic_text = entity_dict.apply(semantic_text, source=source)
        from engines.ai_core.semantic_agent.rule_engine import _apply_full_semantic_polish

        polished = _apply_full_semantic_polish(
            semantic_text,
            source=source,
            tgt_lang=tgt_lang,
            prev_context=prev_context,
            app_dir=app_dir,
        )
        if polished.strip():
            semantic_text = polished
        seg["semantic_text"] = semantic_text
        seg["semantic_model_used"] = "llm" if llm_used else "rule_engine"

        trans_conf = float((seg.get("confidence") or {}).get("translation") or 0.85)
        quality = audit_semantic_output(
            source=source,
            machine_translation=translated,
            semantic_text=semantic_text,
            dialogue=dialogue,
            entity_dict=entity_dict,
            target_lang=tgt_lang,
            translation_confidence=trans_conf,
            llm_used=llm_used,
            model_name=str(seg.get("semantic_model_used") or ""),
        )
        seg["semantic_scores"] = quality.to_dict()
        seg["semantic_quality_passed"] = quality.passed

        per_seg_log = selection.decision_log + [
            f"context={context.score:.3f}",
            f"emotion={emotion.score:.3f}",
            f"semantic_score={quality.semantic_score:.3f}",
        ]
        decision_log.extend([f"segment_{idx}:{d}" for d in per_seg_log])

        final_scores = selection.best.scores
        if meaning.score != final_scores.meaning:
            final_scores = SegmentScores(
                meaning=meaning.score,
                naturalness=final_scores.naturalness,
                context=context.score,
                emotion=emotion.score,
                overall=round(
                    0.40 * meaning.score
                    + 0.25 * final_scores.naturalness
                    + 0.20 * context.score
                    + 0.15 * emotion.score,
                    4,
                ),
            )

        candidates_out = [
            {
                "label": c.variant,
                "text": c.text,
                "source": c.source,
                "scores": c.scores.to_dict(),
            }
            for c in selection.candidates
        ]

        return {
            "index": idx,
            "original_text": source,
            "machine_translation": translated,
            "semantic_text": semantic_text,
            "selected_variant": selection.best.variant,
            "meaning_analysis": analysis,
            "candidates": candidates_out,
            "scores": final_scores.to_dict(),
            "quality": quality.to_dict(),
            "decision_log": per_seg_log,
        }

    def _save_report(
        self,
        manifest: dict[str, Any],
        task_id: str,
        stats: dict[str, Any],
        per_segment: list[dict[str, Any]],
        avg_scores: dict[str, float],
        decision_log: list[str],
        elapsed_ms: float,
        status: str,
        errors: list[str],
        warnings: list[str],
    ) -> Path:
        project_uuid = manifest.get("project_uuid") or "unknown"
        report_dir = self._manifests_dir / project_uuid
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "semantic_report.json"

        report = {
            "semantic_agent_version": self.VERSION,
            "project_uuid": project_uuid,
            "task_id": task_id,
            "status": status,
            "avg_scores": avg_scores,
            "variants_generated": stats.get("variants_generated", 0),
            "llm_used": stats.get("llm_used", False),
            "rule_rewrite_used": stats.get("rule_rewrite_used", False),
            "segment_count": stats.get("segment_count", 0),
            "warnings": warnings + stats.get("warnings", []),
            "errors": errors,
            "execution_time_ms": round(elapsed_ms, 1),
            "decision_log": decision_log,
            "per_segment": per_segment,
        }

        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

        return report_path

    def run(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> AgentExecutionResult:
        """
        Input: state.segments with text (source), translated_text (from Translation Agent)
        Output: segments[].semantic_text ONLY
        Does NOT change: segment count, order, timing, speaker
        """
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []

        try:
            return self._run_impl(manifest, state, task_id, t0, warnings, errors, decision_log)
        except Exception as exc:
            debug_mode = IS_DEBUG_LEARNING_MODE()
            logger.exception("Semantic agent failed: %s", exc)
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "Semantic/v1",
                    called=True,
                    success=False,
                    error=str(exc),
                    decision="LLM skipped",
                    fallback_used=True,
                )
                open_ddf.save(task_id)
            except Exception as ddf_exc:
                logger.debug("OpenDDF record failed: %s", ddf_exc)
            elapsed = (time.perf_counter() - t0) * 1000
            if debug_mode:
                segments = copy.deepcopy(state.get("segments") or [])
                for seg in segments:
                    if not str(seg.get("semantic_text") or "").strip():
                        seg["semantic_text"] = str(seg.get("translated_text") or seg.get("text") or "")
                return AgentExecutionResult(
                    status="warning",
                    updated_state={"segments": segments},
                    metrics={"execution_time_ms": round(elapsed, 1), "debug_mode": True},
                    warnings=warnings + [str(exc)],
                    errors=[],
                    execution_time_ms=round(elapsed, 1),
                    decision_log=decision_log,
                )
            raise

    def _run_impl(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
        t0: float,
        warnings: list[str],
        errors: list[str],
        decision_log: list[str],
    ) -> AgentExecutionResult:
        gate_ok, gate_msgs = self._check_gatekeeper(manifest, state)
        if not gate_ok:
            errors.extend(gate_msgs)
            elapsed = (time.perf_counter() - t0) * 1000
            debug_mode = IS_DEBUG_LEARNING_MODE()
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "Semantic/v1",
                    called=True,
                    success=False,
                    error="; ".join(gate_msgs),
                    fallback_used=debug_mode,
                )
                open_ddf.save(task_id)
            except Exception as exc:
                logger.debug("OpenDDF record failed: %s", exc)
            if debug_mode:
                segments = copy.deepcopy(state.get("segments") or [])
                for seg in segments:
                    if not str(seg.get("semantic_text") or "").strip():
                        seg["semantic_text"] = str(seg.get("translated_text") or seg.get("text") or "")
                return AgentExecutionResult(
                    status="warning",
                    updated_state={"segments": segments},
                    metrics={"execution_time_ms": round(elapsed, 1), "debug_mode": True},
                    warnings=warnings + gate_msgs,
                    errors=[],
                    execution_time_ms=round(elapsed, 1),
                    decision_log=decision_log,
                )
            return AgentExecutionResult(
                status="error",
                updated_state={"segments": state.get("segments") or []},
                metrics={"execution_time_ms": round(elapsed, 1)},
                warnings=warnings,
                errors=errors,
                execution_time_ms=round(elapsed, 1),
                decision_log=decision_log,
            )
        warnings.extend(gate_msgs)

        segments_in = state.get("segments") or []
        segments = copy.deepcopy(segments_in)
        segment_count = len(segments)
        decision_log.append(f"segment_count={segment_count}")

        _source, target = self._source_target(manifest)
        decision_log.append(f"target_lang={target}")

        stats: dict[str, Any] = {
            "segment_count": segment_count,
            "variants_generated": 0,
            "llm_used": False,
            "rule_rewrite_used": False,
            "warnings": [],
        }

        per_segment: list[dict[str, Any]] = []
        quality_log: list[dict[str, Any]] = []
        score_objs: list[SegmentScores] = []

        entity_dict = EntityDictionary.from_segments(segments, target_lang=target, manifest=manifest)

        for i, seg in enumerate(segments):
            dialogue = build_dialogue_context(segments, i, manifest)
            best_report: dict[str, Any] | None = None
            best_text = ""
            best_score = -1.0
            attempts = 0
            last_passed = False

            while attempts < MAX_SEMANTIC_RETRIES:
                force_llm = attempts > 0
                seg_report = self._process_segment(
                    seg,
                    src_lang=_source,
                    tgt_lang=target,
                    dialogue=dialogue,
                    entity_dict=entity_dict,
                    stats=stats,
                    decision_log=decision_log,
                    app_dir=_APP_DIR,
                    force_llm=force_llm,
                )
                attempts += 1
                seg["semantic_retry_count"] = attempts
                quality = seg_report.get("quality") or {}
                last_passed = bool(quality.get("passed"))
                sem_score = float(quality.get("semantic_score") or 0)
                if sem_score > best_score:
                    best_score = sem_score
                    best_report = seg_report
                    best_text = str(seg.get("semantic_text") or "")

                if last_passed or sem_score >= SEMANTIC_SCORE_MIN:
                    break

            if best_report and best_text and not last_passed:
                seg["semantic_text"] = best_text

            if best_report:
                per_segment.append(best_report)
                quality_log.append(
                    {
                        "index": seg.get("index", i),
                        "original_text": best_report.get("original_text"),
                        "machine_translation": best_report.get("machine_translation"),
                        "semantic_text": seg.get("semantic_text"),
                        "errors_found": (best_report.get("quality") or {}).get("issues"),
                        "errors_fixed": (best_report.get("quality") or {}).get("fixes_applied"),
                        "semantic_score": (best_report.get("quality") or {}).get("semantic_score"),
                        "retry_count": attempts,
                        "model_used": (best_report.get("quality") or {}).get("model_used"),
                        "quality": best_report.get("quality"),
                    }
                )
                sc = best_report.get("scores") or {}
                score_objs.append(
                    SegmentScores(
                        meaning=float(sc.get("meaning") or 0),
                        naturalness=float(sc.get("naturalness") or 0),
                        context=float(sc.get("context") or 0),
                        emotion=float(sc.get("emotion") or 0),
                        overall=float(sc.get("overall") or 0),
                    )
                )

        for orig, out in zip(segments_in, segments):
            for key in ("start", "end", "speaker", "text", "index", "translated_text"):
                if key in orig:
                    out[key] = orig[key]
            if not str(out.get("semantic_text") or "").strip():
                out["semantic_text"] = str(out.get("translated_text") or orig.get("text") or "")
                stats["warnings"].append(
                    f"segment_{out.get('index', '?')}:empty_semantic_fallback"
                )

        avg_scores = aggregate_averages(score_objs)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        status = "error" if errors else ("warning" if warnings or stats.get("warnings") else "success")
        if IS_DEBUG_LEARNING_MODE() and status == "error":
            status = "warning"

        report_path = self._save_report(
            manifest,
            task_id,
            stats,
            per_segment,
            avg_scores,
            decision_log,
            elapsed_ms,
            status,
            errors,
            warnings,
        )

        quality_summary = {
            "passed_count": sum(1 for q in quality_log if (q.get("quality") or {}).get("passed")),
            "failed_count": sum(1 for q in quality_log if not (q.get("quality") or {}).get("passed")),
            "avg_semantic_score": round(
                sum(float((q.get("quality") or {}).get("semantic_score") or 0) for q in quality_log)
                / max(1, len(quality_log)),
                4,
            ),
            "retries_total": sum(int(q.get("retry_count") or 0) for q in quality_log),
        }
        quality_report_path = write_semantic_quality_report(
            task_id,
            quality_log,
            summary=quality_summary,
            project_uuid=str(manifest.get("project_uuid") or ""),
            app_dir=self.output_dir.parent if self.output_dir.name == "output" else _APP_DIR,
        )

        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "Semantic/v4.0",
                called=True,
                success=status != "error",
                decision="llm" if stats.get("llm_used") else "rule_engine",
                error="; ".join(errors) if errors else None,
                fallback_used=not stats.get("llm_used", False),
                output_metrics=quality_summary,
            )
            open_ddf.save(task_id)
        except Exception as exc:
            logger.debug("OpenDDF record failed: %s", exc)

        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "semantic_report_path": str(report_path),
                "semantic_quality_report_path": str(quality_report_path),
                "semantic_quality_summary": quality_summary,
                "project_uuid": manifest.get("project_uuid"),
            },
            metrics={
                "execution_time_ms": round(elapsed_ms, 1),
                "segment_count": segment_count,
                "variants_generated": stats["variants_generated"],
                "llm_used": stats["llm_used"],
                "rule_rewrite_used": stats["rule_rewrite_used"],
                "avg_scores": avg_scores,
                "per_segment": per_segment,
            },
            warnings=warnings + stats.get("warnings", []),
            errors=errors,
            execution_time_ms=round(elapsed_ms, 1),
            decision_log=decision_log,
        )


__all__ = ["SemanticAgent", "load_manifest"]
