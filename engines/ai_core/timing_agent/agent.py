"""TubeDub Timing Agent v1.0 — adapt text to timing slots."""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.timing_agent.candidate_selector import needs_adaptation
from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
from engines.ai_core.timing_agent.retry_policy import apply_retry_policy
from engines.ai_core.timing_agent.scoring import TimingScores, aggregate_averages
from engines.ai_core.translation_agent.agent import load_manifest
from engines.mt.lang_codes import normalize_lang
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

logger = logging.getLogger("tubedub.ai_core.timing_agent")

_APP_DIR = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _APP_DIR / "output"
_MANIFESTS_DIR = _OUTPUT_DIR / "manifests"


def _slot_ms(seg: dict[str, Any]) -> int:
    brief = seg.get("creative_brief") or {}
    if brief.get("maximum_duration_ms"):
        return max(500, int(brief["maximum_duration_ms"]))
    start = seg.get("start")
    end = seg.get("end")
    if start is not None and end is not None:
        return max(500, int(end) - int(start))
    raw = int(seg.get("slot_ms") or 0)
    return max(500, raw) if raw > 0 else 3000


def _brief_timing_tolerance(seg: dict[str, Any]) -> tuple[float, float]:
    """Return (compression, expansion) allowances from creative brief."""
    brief = seg.get("creative_brief") or {}
    if not brief:
        return 0.35, 0.15
    return (
        float(brief.get("allowed_compression", 0.35)),
        float(brief.get("allowed_expansion", 0.15)),
    )


class TimingAgent:
    """Multi-pass timing adaptation — writes segments[].timing_text only."""

    VERSION = "1.0"

    def __init__(
        self,
        output_dir: Path | None = None,
        *,
        use_llm: bool = True,
    ):
        self.output_dir = output_dir or _OUTPUT_DIR
        self._manifests_dir = self.output_dir / "manifests"
        self.use_llm = use_llm

    def _check_gatekeeper(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        from engines.ai_core.gatekeeper_peer import check_upstream_gate

        ok, errors, warnings = check_upstream_gate("timing", manifest, state)
        return ok, errors + warnings

    def _source_target(self, manifest: dict[str, Any]) -> tuple[str, str]:
        src = normalize_lang(manifest.get("source_lang") or "en")
        tgt = normalize_lang(manifest.get("target_lang") or "ru")
        return src, tgt

    def _process_segment(
        self,
        seg: dict[str, Any],
        *,
        tgt_lang: str,
        stats: dict[str, Any],
        decision_log: list[str],
    ) -> dict[str, Any]:
        source = str(seg.get("text") or "").strip()
        semantic = str(
            seg.get("semantic_text") or seg.get("translated_text") or ""
        ).strip()
        idx = seg.get("index", "?")
        slot_ms = _slot_ms(seg)
        brief = seg.get("creative_brief") or {}
        if brief.get("preferred_duration_ms"):
            slot_ms = min(slot_ms, max(1, int(brief["preferred_duration_ms"])))
        allowed_compression, allowed_expansion = _brief_timing_tolerance(seg)
        decision_log.append(
            f"segment_{idx}:brief_compression={allowed_compression:.2f} expansion={allowed_expansion:.2f}"
        )

        if not semantic:
            seg["timing_text"] = source or semantic
            decision_log.append(f"segment_{idx}:empty_semantic_fallback")
            return {
                "index": idx,
                "original_ms": 0,
                "predicted_ms": 0,
                "slot_ms": slot_ms,
                "timing_text": seg["timing_text"],
                "selected_variant": "fallback",
                "slot_fit_score": 0.0,
                "attempts": 0,
                "candidates": [],
            }

        original_ms = predict_duration_ms(semantic, tgt_lang)
        direction = needs_adaptation(original_ms, slot_ms)
        decision_log.append(
            f"segment_{idx}:pass1 predicted={original_ms} slot={slot_ms} dir={direction}"
        )

        retry = apply_retry_policy(
            semantic,
            source=source,
            slot_ms=slot_ms,
            tgt_lang=tgt_lang,
            use_llm=self.use_llm,
            allowed_compression=allowed_compression,
            allowed_expansion=allowed_expansion,
        )
        decision_log.extend([f"segment_{idx}:{d}" for d in retry.decision_log])

        if retry.rule_rewrite_used:
            stats["rule_rewrite_used"] = True
        if retry.llm_rewrite_used:
            stats["llm_rewrite_used"] = True
        if retry.micro_stretch_recommended:
            stats["micro_stretch_segments"] = stats.get("micro_stretch_segments", 0) + 1
            seg["micro_stretch_recommended"] = True
        if retry.used_fallback:
            stats["warnings"].append(f"segment_{idx}:fallback_semantic_text")

        timing_text = str(retry.text or "").strip() or semantic
        seg["timing_text"] = timing_text
        seg["slot_fit_score"] = round(float(retry.slot_fit_score or 0), 4)
        seg["timing_predicted_ms"] = retry.predicted_ms
        seg["timing_slot_ms"] = slot_ms

        return {
            "index": idx,
            "original_ms": original_ms,
            "predicted_ms": retry.predicted_ms,
            "slot_ms": slot_ms,
            "semantic_text": semantic,
            "timing_text": timing_text,
            "selected_variant": retry.selected_variant,
            "slot_fit_score": retry.slot_fit_score,
            "attempts": retry.attempts,
            "direction": direction,
            "rule_rewrite_used": retry.rule_rewrite_used,
            "llm_rewrite_used": retry.llm_rewrite_used,
            "micro_stretch_recommended": retry.micro_stretch_recommended,
            "candidates": retry.candidates,
            "decision_log": retry.decision_log,
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
        report_path = report_dir / "timing_report.json"

        report = {
            "timing_agent_version": self.VERSION,
            "openddf_agent": "Timing/v1",
            "project_uuid": project_uuid,
            "task_id": task_id,
            "status": status,
            "avg_scores": avg_scores,
            "rule_rewrite_used": stats.get("rule_rewrite_used", False),
            "llm_rewrite_used": stats.get("llm_rewrite_used", False),
            "micro_stretch_used": stats.get("micro_stretch_segments", 0) > 0,
            "micro_stretch_segments": stats.get("micro_stretch_segments", 0),
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
        Adapt segment text to fit timing slots.
        Input: segments with semantic_text, start/end (slot_ms)
        Output: segments[].timing_text ONLY
        """
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []

        try:
            return self._run_impl(manifest, state, task_id, t0, warnings, errors, decision_log)
        except Exception as exc:
            debug_mode = IS_DEBUG_LEARNING_MODE()
            logger.exception("Timing agent failed: %s", exc)
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "Timing/v1",
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
                    if not str(seg.get("timing_text") or "").strip():
                        seg["timing_text"] = str(
                            seg.get("semantic_text") or seg.get("translated_text") or ""
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
                    "Timing/v1",
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
                    if not str(seg.get("timing_text") or "").strip():
                        seg["timing_text"] = str(
                            seg.get("semantic_text") or seg.get("translated_text") or ""
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
            "rule_rewrite_used": False,
            "llm_rewrite_used": False,
            "micro_stretch_segments": 0,
            "warnings": [],
        }

        per_segment: list[dict[str, Any]] = []
        score_objs: list[TimingScores] = []

        for seg in segments:
            seg_report = self._process_segment(
                seg,
                tgt_lang=target,
                stats=stats,
                decision_log=decision_log,
            )
            per_segment.append(seg_report)
            score_objs.append(
                TimingScores(
                    slot_fit=float(seg_report.get("slot_fit_score") or 0),
                    meaning=1.0,
                    naturalness=0.85,
                    integrity=1.0,
                    timing=float(seg_report.get("slot_fit_score") or 0),
                    overall=float(seg_report.get("slot_fit_score") or 0),
                )
            )

        preserve_keys = (
            "start",
            "end",
            "speaker",
            "text",
            "index",
            "translated_text",
            "semantic_text",
        )
        for orig, out in zip(segments_in, segments):
            for key in preserve_keys:
                if key in orig:
                    out[key] = orig[key]
            if not str(out.get("timing_text") or "").strip():
                fallback = str(out.get("semantic_text") or orig.get("translated_text") or "")
                out["timing_text"] = fallback
                stats["warnings"].append(
                    f"segment_{out.get('index', '?')}:empty_timing_fallback"
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
                "Timing/v1",
                called=True,
                success=status != "error",
                decision="llm" if stats.get("llm_rewrite_used") else "LLM skipped",
                error="; ".join(errors) if errors else None,
                fallback_used=not stats.get("llm_rewrite_used", False),
            )
            open_ddf.save(task_id)
        except Exception as exc:
            logger.debug("OpenDDF record failed: %s", exc)

        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "timing_report_path": str(report_path),
                "project_uuid": manifest.get("project_uuid"),
            },
            metrics={
                "execution_time_ms": round(elapsed_ms, 1),
                "segment_count": segment_count,
                "rule_rewrite_used": stats["rule_rewrite_used"],
                "llm_rewrite_used": stats["llm_rewrite_used"],
                "micro_stretch_segments": stats["micro_stretch_segments"],
                "avg_scores": avg_scores,
                "per_segment": per_segment,
            },
            warnings=warnings + stats.get("warnings", []),
            errors=errors,
            execution_time_ms=round(elapsed_ms, 1),
            decision_log=decision_log,
        )


__all__ = ["TimingAgent", "load_manifest"]
