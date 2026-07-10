"""TubeDub Grammar Agent v1.0 — grammar polish preserving timing length."""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.grammar_agent.candidate_selector import (
    generate_candidates,
    select_best_candidate,
)
from engines.ai_core.grammar_agent.natural_speech import to_conversational
from engines.ai_core.grammar_agent.pronunciation_optimizer import optimize_pronunciation
from engines.ai_core.grammar_agent.retry_policy import (
    MEANING_THRESHOLD,
    apply_retry_policy,
)
from engines.ai_core.grammar_agent.rule_engine import (
    apply_grammar_pass,
    apply_style_pass,
    apply_syntax_pass,
)
from engines.ai_core.grammar_agent.scoring import SegmentScores, aggregate_averages, length_within_tolerance
from engines.ai_core.grammar_agent.validators.meaning_preservation import validate_meaning_preservation
from engines.ai_core.translation_agent.agent import load_manifest
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE
from engines.mt.lang_codes import normalize_lang
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

logger = logging.getLogger("tubedub.ai_core.grammar_agent")

_APP_DIR = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _APP_DIR / "output"
_MANIFESTS_DIR = _OUTPUT_DIR / "manifests"


def _brief_grammar_style(seg: dict[str, Any]) -> dict[str, float | str]:
    brief = seg.get("creative_brief") or {}
    if not brief:
        return {}
    return {
        "speech_style": str(brief.get("speech_style") or "conversational"),
        "naturalness_priority": float(brief.get("naturalness_priority", 0.75)),
        "formality": float(brief.get("formality", 0.5)),
    }


class GrammarAgent:
    """Multi-pass grammar polish — writes segments[].grammar_text only."""

    VERSION = "1.0"

    def __init__(
        self,
        output_dir: Path | None = None,
        *,
        use_llm: bool = True,
        meaning_threshold: float = MEANING_THRESHOLD,
    ):
        self.output_dir = output_dir or _OUTPUT_DIR
        self._manifests_dir = self.output_dir / "manifests"
        self.use_llm = use_llm
        self.meaning_threshold = meaning_threshold

    def _check_gatekeeper(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        from engines.ai_core.gatekeeper_peer import check_upstream_gate

        ok, errors, warnings = check_upstream_gate("grammar", manifest, state)
        return ok, errors + warnings

    def _source_target(self, manifest: dict[str, Any]) -> tuple[str, str]:
        src = normalize_lang(manifest.get("source_lang") or "en")
        tgt = normalize_lang(manifest.get("target_lang") or "ru")
        return src, tgt

    def _multi_pass(
        self,
        timing_text: str,
        *,
        source: str,
        tgt_lang: str,
        prev_context: str | None,
        brief_style: dict[str, float | str] | None = None,
    ) -> str:
        """Passes 1-5: grammar → syntax → style → natural speech → pronunciation."""
        text = str(timing_text or "").strip()
        if not text:
            return text

        text = apply_grammar_pass(text, tgt_lang=tgt_lang)
        text = apply_syntax_pass(text, tgt_lang=tgt_lang)
        text = apply_style_pass(text, tgt_lang=tgt_lang)
        speech_style = str((brief_style or {}).get("speech_style") or "conversational")
        if speech_style != "formal":
            text = to_conversational(text, tgt_lang=tgt_lang, prev_context=prev_context)
        text = optimize_pronunciation(text, tgt_lang=tgt_lang)

        if not length_within_tolerance(timing_text, text):
            return str(timing_text or "").strip()
        return text.strip() or str(timing_text or "").strip()

    def _process_segment(
        self,
        seg: dict[str, Any],
        *,
        tgt_lang: str,
        prev_context: str | None,
        stats: dict[str, Any],
        decision_log: list[str],
    ) -> dict[str, Any]:
        source = str(seg.get("text") or "").strip()
        timing = str(
            seg.get("timing_text") or seg.get("semantic_text") or seg.get("translated_text") or ""
        ).strip()
        idx = seg.get("index", "?")
        brief_style = _brief_grammar_style(seg)
        if brief_style:
            decision_log.append(
                f"segment_{idx}:brief_style={brief_style.get('speech_style')} "
                f"naturalness={brief_style.get('naturalness_priority')}"
            )

        if not timing:
            seg["grammar_text"] = timing
            decision_log.append(f"segment_{idx}:empty_timing_fallback")
            return {
                "index": idx,
                "timing_text": timing,
                "grammar_text": timing,
                "selected_variant": "fallback",
                "scores": SegmentScores(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5).to_dict(),
                "candidates": [],
                "decision_log": ["empty_timing"],
            }

        working = self._multi_pass(
            timing,
            source=source,
            tgt_lang=tgt_lang,
            prev_context=prev_context,
            brief_style=brief_style,
        )
        decision_log.append(f"segment_{idx}:passes1-5 complete len={len(working)}")

        variants, llm_used = generate_candidates(
            source,
            working,
            tgt_lang=tgt_lang,
            use_llm=self.use_llm,
        )
        stats["variants_generated"] += len(variants)
        if llm_used:
            stats["llm_used"] = True
        stats["rule_rewrite_used"] = True

        selection = select_best_candidate(
            source,
            timing,
            variants,
            tgt_lang=tgt_lang,
            llm_used=llm_used,
        )

        candidate_text = selection.best.text
        meaning = validate_meaning_preservation(source, timing, candidate_text)

        if meaning.score < self.meaning_threshold or not length_within_tolerance(timing, candidate_text):
            retry = apply_retry_policy(
                source,
                timing,
                candidate_text,
                meaning.score,
                tgt_lang=tgt_lang,
            )
            decision_log.extend([f"segment_{idx}:{d}" for d in retry.decision_log])
            candidate_text = retry.text
            meaning = validate_meaning_preservation(source, timing, candidate_text)
            if retry.used_fallback:
                stats["warnings"].append(f"segment_{idx}:fallback_timing_text")

        grammar_text = str(candidate_text or "").strip() or timing
        if not length_within_tolerance(timing, grammar_text):
            grammar_text = timing
            stats["warnings"].append(f"segment_{idx}:length_guard_timing_text")
            decision_log.append(f"segment_{idx}:length_guard_reject")

        final_scores = selection.best.scores
        if meaning.score != final_scores.meaning:
            final_scores = SegmentScores(
                grammar=final_scores.grammar,
                syntax=final_scores.syntax,
                style=final_scores.style,
                naturalness=final_scores.naturalness,
                pronunciation=final_scores.pronunciation,
                readability=final_scores.readability,
                meaning=meaning.score,
                overall=round(
                    0.20 * final_scores.grammar
                    + 0.15 * final_scores.syntax
                    + 0.10 * final_scores.style
                    + 0.15 * final_scores.naturalness
                    + 0.10 * final_scores.pronunciation
                    + 0.10 * final_scores.readability
                    + 0.20 * meaning.score,
                    4,
                ),
            )

        seg["grammar_text"] = grammar_text
        scores_dict = final_scores.to_dict()
        seg["grammar_scores"] = scores_dict
        seg["grammar_score"] = round(float(scores_dict.get("grammar") or 0), 4)

        per_seg_log = selection.decision_log + [f"meaning={meaning.score:.3f}"]
        decision_log.extend([f"segment_{idx}:{d}" for d in per_seg_log])

        candidates_out = [
            {
                "label": c.variant,
                "text": c.text,
                "source": c.source,
                "length_ratio": c.length_ratio,
                "scores": c.scores.to_dict(),
            }
            for c in selection.candidates
        ]

        return {
            "index": idx,
            "timing_text": timing,
            "grammar_text": grammar_text,
            "selected_variant": selection.best.variant,
            "candidates": candidates_out,
            "scores": final_scores.to_dict(),
            "decision_log": per_seg_log,
            "llm_used": llm_used,
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
        report_path = report_dir / "grammar_report.json"

        report = {
            "grammar_agent_version": self.VERSION,
            "openddf_agent": "Grammar/v1",
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
        Input: segments with timing_text (from Timing Agent)
        Output: segments[].grammar_text ONLY

        Multi-pass:
          Pass 1: Grammar Correction (rule)
          Pass 2: Syntax Improvement
          Pass 3: Style Improvement
          Pass 4: Natural Speech
          Pass 5: Pronunciation Optimization
          Final: grammar_text from best of 3 candidates

        MUST NOT change text length significantly (±10% max vs timing_text)
        MUST NOT change facts/entities
        Fallback: timing_text if all fails
        """
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []

        try:
            return self._run_impl(manifest, state, task_id, t0, warnings, errors, decision_log)
        except Exception as exc:
            debug_mode = IS_DEBUG_LEARNING_MODE()
            logger.exception("Grammar agent failed: %s", exc)
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "Grammar/v1",
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
                    if not str(seg.get("grammar_text") or "").strip():
                        seg["grammar_text"] = str(
                            seg.get("timing_text") or seg.get("semantic_text") or ""
                        )
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
                    "Grammar/v1",
                    called=True,
                    success=False,
                    error="; ".join(gate_msgs),
                    fallback_used=debug_mode,
                )
                open_ddf.save(task_id)
            except Exception as rec_exc:
                logger.debug("OpenDDF record failed: %s", rec_exc)
            if debug_mode:
                segments = copy.deepcopy(state.get("segments") or [])
                for seg in segments:
                    if not str(seg.get("grammar_text") or "").strip():
                        seg["grammar_text"] = str(
                            seg.get("timing_text") or seg.get("semantic_text") or ""
                        )
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
        score_objs: list[SegmentScores] = []
        prev_context: str | None = None

        for seg in segments:
            seg_report = self._process_segment(
                seg,
                tgt_lang=target,
                prev_context=prev_context,
                stats=stats,
                decision_log=decision_log,
            )
            per_segment.append(seg_report)
            sc = seg_report.get("scores") or {}
            score_objs.append(
                SegmentScores(
                    grammar=float(sc.get("grammar") or 0),
                    syntax=float(sc.get("syntax") or 0),
                    style=float(sc.get("style") or 0),
                    naturalness=float(sc.get("naturalness") or 0),
                    pronunciation=float(sc.get("pronunciation") or 0),
                    readability=float(sc.get("readability") or 0),
                    meaning=float(sc.get("meaning") or 0),
                    overall=float(sc.get("overall") or 0),
                )
            )
            prev_context = str(seg.get("grammar_text") or "")

        preserve_keys = (
            "start",
            "end",
            "speaker",
            "text",
            "index",
            "translated_text",
            "semantic_text",
            "timing_text",
        )
        for orig, out in zip(segments_in, segments):
            for key in preserve_keys:
                if key in orig:
                    out[key] = orig[key]
            if not str(out.get("grammar_text") or "").strip():
                fallback = str(out.get("timing_text") or orig.get("semantic_text") or "")
                out["grammar_text"] = fallback
                stats["warnings"].append(
                    f"segment_{out.get('index', '?')}:empty_grammar_fallback"
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

        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "Grammar/v1",
                called=True,
                success=status != "error",
                decision="llm" if stats.get("llm_used") else "LLM skipped",
                error="; ".join(errors) if errors else None,
                fallback_used=not stats.get("llm_used", False),
            )
            open_ddf.save(task_id)
        except Exception as exc:
            logger.debug("OpenDDF record failed: %s", exc)

        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "grammar_report_path": str(report_path),
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


__all__ = ["GrammarAgent", "load_manifest"]
