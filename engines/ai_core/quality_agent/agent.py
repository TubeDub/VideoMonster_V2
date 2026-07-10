"""TubeDub Quality Agent v1.0 — read-only auditor + retry router."""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.quality_agent import smart_router
from engines.ai_core.quality_agent.decision_engine import decide
from engines.ai_core.quality_agent.retry_orchestrator import MAX_RETRIES
from engines.ai_core.quality_agent.scoring import aggregate_averages
from engines.ai_core.quality_agent.segment_auditor import audit_segment
from engines.ai_core.translation_agent.agent import load_manifest
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.quality_agent")

_APP_DIR = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _APP_DIR / "output"
_MANIFESTS_DIR = _OUTPUT_DIR / "manifests"

_TEXT_FIELDS = (
    "text",
    "translated_text",
    "semantic_text",
    "timing_text",
    "grammar_text",
)


def _brief_quality_thresholds(seg: dict[str, Any]) -> dict[str, float] | None:
    brief = seg.get("creative_brief") or {}
    if not brief:
        return None
    return {
        "meaning_priority": float(brief.get("meaning_priority", 0.95)),
        "naturalness_priority": float(brief.get("naturalness_priority", 0.75)),
        "lip_sync_priority": float(brief.get("lip_sync_priority", 0.7)),
    }


class QualityAgent:
    """Read-only quality auditor + smart retry router."""

    VERSION = "1.0"

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or _OUTPUT_DIR
        self._manifests_dir = self.output_dir / "manifests"

    def _check_gatekeeper(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        """Require Grammar Agent success before quality audit."""
        warnings: list[str] = []
        if not manifest.get("project_uuid"):
            return False, ["planner_not_complete:missing_project_uuid"]

        segments = state.get("segments") or []
        if not segments:
            return False, ["grammar_not_complete:no_segments"]

        grammar_status = str(state.get("grammar_agent_status") or "success")
        if grammar_status == "error":
            return False, ["grammar_agent_failed"]

        if grammar_status == "warning":
            warnings.append("grammar_agent_warning")

        has_grammar = any(str(s.get("grammar_text") or "").strip() for s in segments)
        if not has_grammar:
            return False, ["grammar_not_complete:no_grammar_text"]

        return True, warnings

    def _load_timing_report(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        project_uuid = manifest.get("project_uuid")
        if not project_uuid:
            return None
        path = self._manifests_dir / project_uuid / "timing_report.json"
        if not path.is_file():
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _save_report(
        self,
        manifest: dict[str, Any],
        task_id: str,
        per_segment: list[dict[str, Any]],
        summary: dict[str, Any],
        avg_scores: dict[str, float],
        elapsed_ms: float,
        status: str,
        errors: list[str],
        warnings: list[str],
        decision_log: list[str],
    ) -> Path:
        project_uuid = manifest.get("project_uuid") or "unknown"
        report_dir = self._manifests_dir / project_uuid
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "quality_report.json"

        report = {
            "quality_agent_version": self.VERSION,
            "openddf_agent": "Quality/v1",
            "project_uuid": project_uuid,
            "task_id": task_id,
            "status": status,
            "avg_scores": avg_scores,
            "summary": summary,
            "segment_count": len(per_segment),
            "warnings": warnings,
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
        Input: segments with grammar_text, translated_text, semantic_text, timing_text, start/end
        Output: segments with quality_decision, quality_scores, quality_passed (bool)
        Does NOT modify text fields (retry agents may update text during routing).

        For each segment:
          1. Run full audit (segment_auditor)
          2. decision_engine → ACCEPT|RETRY|FALLBACK|WARNING|FAIL
          3. If RETRY and retries < 3: smart_router re-runs ONLY responsible agent on THIS segment
          4. Re-audit after retry
          5. If still fail after 3: FALLBACK (use best available text) or WARNING or FAIL if critical
        """
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []
        debug_mode = IS_DEBUG_LEARNING_MODE()

        gate_ok, gate_msgs = self._check_gatekeeper(manifest, state)
        if not gate_ok:
            errors.extend(gate_msgs)
            elapsed = (time.perf_counter() - t0) * 1000
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "Quality/v1",
                    called=True,
                    success=False,
                    error="; ".join(gate_msgs),
                    fallback_used=debug_mode,
                    decision="gatekeeper_fail",
                )
                open_ddf.save(task_id)
            except Exception as exc:
                logger.debug("OpenDDF record failed: %s", exc)
            if debug_mode:
                warnings.extend(gate_msgs)
                segments = copy.deepcopy(state.get("segments") or [])
                for seg in segments:
                    seg["quality_decision"] = "WARNING"
                    seg["quality_passed"] = True
                    seg["quality_reasons"] = ["debug_mode_gatekeeper_bypass"]
                return AgentExecutionResult(
                    status="warning",
                    updated_state={"segments": segments, "quality_summary": {"warnings": len(segments)}},
                    metrics={"execution_time_ms": round(elapsed, 1), "debug_mode": True},
                    warnings=warnings,
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

        source_lang = normalize_lang(manifest.get("source_lang") or "en")
        target_lang = normalize_lang(manifest.get("target_lang") or "ru")
        timing_report = self._load_timing_report(manifest)
        working_state = {
            "segments": segments,
            **{k: v for k, v in state.items() if k != "segments"},
        }

        per_segment_reports: list[dict[str, Any]] = []
        audit_results = []
        summary = {"accepted": 0, "retried": 0, "failed": 0, "warnings": 0, "fallback": 0}
        quality_fail_downgraded = 0

        for seg_idx, seg in enumerate(segments):
            retry_count = 0
            routed_to: str | None = None
            final_decision = "ACCEPT"
            final_reasons: list[str] = []
            audit = audit_segment(
                seg,
                all_segments=segments,
                source_lang=source_lang,
                target_lang=target_lang,
                timing_report=timing_report,
                brief_thresholds=_brief_quality_thresholds(seg),
            )

            while True:
                seg_decision = decide(
                    audit,
                    retry_count=retry_count,
                    max_retries=MAX_RETRIES,
                    debug_mode=debug_mode,
                )
                final_decision = seg_decision.decision
                final_reasons = seg_decision.reasons

                if seg_decision.decision == "RETRY":
                    retry_count += 1
                    summary["retried"] += 1
                    updated, agent_name = smart_router.route_and_fix_segment(
                        seg,
                        seg_decision.failure_type,
                        manifest,
                        working_state,
                        task_id,
                        segment_index=seg_idx,
                    )
                    if updated:
                        segments[seg_idx] = updated
                        seg = updated
                    routed_to = agent_name
                    decision_log.append(
                        f"segment_{seg.get('index', seg_idx)}:retry={retry_count} "
                        f"agent={agent_name} failure={seg_decision.failure_type}"
                    )
                    audit = audit_segment(
                        seg,
                        all_segments=segments,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        timing_report=timing_report,
                        brief_thresholds=_brief_quality_thresholds(seg),
                    )
                    continue

                break

            if final_decision == "FAIL" and debug_mode:
                final_decision = "WARNING"
                final_reasons = list(final_reasons) + ["quality_fail_downgraded_to_warning"]
                quality_fail_downgraded += 1

            if "quality_fail_downgraded_to_warning" in final_reasons:
                try:
                    from engines.open_ddf import open_ddf

                    open_ddf.record_agent(
                        task_id,
                        "Quality/v1",
                        called=True,
                        success=True,
                        decision="quality_fail_downgraded_to_warning",
                        fallback_used=True,
                        segment_idx=seg_idx,
                    )
                except Exception as exc:
                    logger.debug("OpenDDF downgrade record failed: %s", exc)

            audit_results.append(audit)
            quality_passed = final_decision in ("ACCEPT", "WARNING", "FALLBACK")

            if final_decision == "ACCEPT":
                summary["accepted"] += 1
            elif final_decision == "WARNING":
                summary["warnings"] += 1
            elif final_decision == "FALLBACK":
                summary["fallback"] += 1
            elif final_decision == "FAIL":
                summary["failed"] += 1

            seg["quality_decision"] = final_decision
            seg["quality_scores"] = audit.scores.to_dict()
            seg["quality_passed"] = quality_passed
            seg["quality_retry_count"] = retry_count
            seg["quality_routed_to_agent"] = routed_to
            seg["quality_reasons"] = final_reasons
            if final_decision == "FALLBACK":
                seg["quality_fallback_text"] = str(
                    seg.get("grammar_text")
                    or seg.get("timing_text")
                    or seg.get("semantic_text")
                    or ""
                ).strip()

            per_segment_reports.append(
                {
                    "index": seg.get("index", seg_idx),
                    "scores": audit.scores.to_dict(),
                    "decision": final_decision,
                    "quality_passed": quality_passed,
                    "retry_count": retry_count,
                    "routed_to_agent": routed_to,
                    "reasons": final_reasons,
                    "failure_types": audit.failure_types,
                    "checks": audit.checks,
                }
            )
            decision_log.append(
                f"segment_{seg.get('index', seg_idx)}:decision={final_decision} "
                f"overall={audit.scores.overall:.3f}"
            )

        avg_scores = aggregate_averages(audit_results)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        status = (
            "error"
            if summary["failed"] > 0 and not debug_mode
            else ("warning" if summary["warnings"] or summary["fallback"] > 0 else "success")
        )

        report_path = self._save_report(
            manifest,
            task_id,
            per_segment_reports,
            summary,
            avg_scores,
            elapsed_ms,
            status,
            errors,
            warnings,
            decision_log,
        )

        try:
            from engines.open_ddf import open_ddf

            ddf_decision = "audit"
            if quality_fail_downgraded > 0:
                ddf_decision = "quality_fail_downgraded_to_warning"
            open_ddf.record_agent(
                task_id,
                "Quality/v1",
                called=True,
                success=True,
                decision=ddf_decision,
                error="; ".join(errors) if errors else None,
                fallback_used=summary["fallback"] > 0 or quality_fail_downgraded > 0,
            )
            open_ddf.save(task_id)
        except Exception as exc:
            logger.debug("OpenDDF record failed: %s", exc)

        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "quality_report_path": str(report_path),
                "project_uuid": manifest.get("project_uuid"),
                "quality_summary": summary,
            },
            metrics={
                "execution_time_ms": round(elapsed_ms, 1),
                "segment_count": len(segments),
                "summary": summary,
                "avg_scores": avg_scores,
                "per_segment": per_segment_reports,
                "debug_mode": debug_mode,
            },
            warnings=warnings,
            errors=errors,
            execution_time_ms=round(elapsed_ms, 1),
            decision_log=decision_log,
        )


__all__ = ["QualityAgent", "load_manifest"]
