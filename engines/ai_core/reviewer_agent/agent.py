"""Reviewer Agent — final gate before Voice: completeness, language, meaning heuristics."""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.debug_helpers import finalize_agent_status
from engines.ai_core.reviewer_loop import (
    run_reviewer_loop_for_segments,
    write_reviewer_loop_json,
)
from engines.ai_core.translation_agent.translator_interface import TranslatorRegistry
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE
from engines.dub_quality_stabilization import MAX_REVIEWER_RETRIES
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.reviewer_agent")

_APP_DIR = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _APP_DIR / "output"


class ReviewerAgent:
    """Pre-voice reviewer — route failed segments back to responsible agents."""

    VERSION = "1.1"

    def __init__(
        self,
        output_dir: Path | None = None,
        *,
        max_retries: int = MAX_REVIEWER_RETRIES,
    ):
        self.output_dir = output_dir or _OUTPUT_DIR
        self._manifests_dir = self.output_dir / "manifests"
        self.max_retries = max_retries

    def _save_report(
        self,
        manifest: dict[str, Any],
        task_id: str,
        per_segment: list[dict[str, Any]],
        summary: dict[str, Any],
        elapsed_ms: float,
        status: str,
        warnings: list[str],
        errors: list[str],
        decision_log: list[str],
    ) -> Path:
        project_uuid = manifest.get("project_uuid") or "unknown"
        report_dir = self._manifests_dir / project_uuid
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "reviewer_report.json"
        report = {
            "reviewer_agent_version": self.VERSION,
            "project_uuid": project_uuid,
            "task_id": task_id,
            "status": status,
            "summary": summary,
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
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []

        segments_in = state.get("segments") or []
        if not segments_in:
            return AgentExecutionResult(
                status="warning",
                updated_state={"segments": []},
                metrics={},
                warnings=["no_segments"],
                errors=[],
                execution_time_ms=0.0,
                decision_log=["no_segments"],
            )

        segments = copy.deepcopy(segments_in)
        source_lang = normalize_lang(manifest.get("source_lang") or "en")
        target_lang = normalize_lang(manifest.get("target_lang") or "ru")
        registry = TranslatorRegistry(manifest.get("capability_matrix") or {})

        working_state = dict(state)
        working_state["segments"] = segments

        segments, loop_log = run_reviewer_loop_for_segments(
            segments,
            manifest=manifest,
            state=working_state,
            task_id=task_id,
            source_lang=source_lang,
            target_lang=target_lang,
            registry=registry,
            max_retries=self.max_retries,
        )

        for entry in loop_log:
            idx = entry.get("index")
            for ev in entry.get("events") or []:
                decision_log.append(
                    f"segment_{idx}:retry={ev.get('attempt')} "
                    f"route={ev.get('route_to')} issues={ev.get('issues')}"
                )
                if ev.get("routed_agent"):
                    decision_log.append(
                        f"segment_{idx}:routed_to={ev.get('routed_agent')}"
                    )
                for action in ev.get("inline_actions") or []:
                    decision_log.append(f"segment_{idx}:{action}")

        passed = sum(1 for r in loop_log if r.get("pass"))
        retried = sum(int(r.get("retry_count") or 0) for r in loop_log)

        per_segment = [
            {
                **(entry.get("final_audit") or {}),
                "retry_count": entry.get("retry_count"),
                "events": entry.get("events"),
            }
            for entry in loop_log
        ]

        for entry in loop_log:
            if not entry.get("pass"):
                warnings.append(
                    f"segment_{entry.get('index')}:review_failed "
                    f"issues={(entry.get('final_audit') or {}).get('issues')}"
                )

        elapsed_ms = (time.perf_counter() - t0) * 1000
        summary = {
            "segment_count": len(segments),
            "passed_count": passed,
            "failed_count": len(segments) - passed,
            "retries": retried,
        }
        status = "success" if passed == len(segments) else "warning"
        if passed == 0 and segments and not IS_DEBUG_LEARNING_MODE():
            status = "error"
            errors.append("all_segments_failed_review")
        status = finalize_agent_status(status)

        report_path = self._save_report(
            manifest,
            task_id,
            per_segment,
            summary,
            elapsed_ms,
            status,
            warnings,
            errors,
            decision_log,
        )

        try:
            write_reviewer_loop_json(
                task_id,
                loop_log,
                project_uuid=str(manifest.get("project_uuid") or ""),
                app_dir=self.output_dir.parent if self.output_dir.name == "output" else _APP_DIR,
            )
        except Exception as exc:
            logger.debug("reviewer_loop.json skipped: %s", exc)

        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "Reviewer/v1.1",
                called=True,
                success=status != "error",
                decision=f"passed={passed}/{len(segments)}",
                retry_count=retried,
                output_metrics=summary,
            )
            open_ddf.save(task_id)
        except Exception:
            pass

        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "reviewer_agent_path": True,
                "reviewer_report_path": str(report_path),
                "reviewer_report": summary,
                "reviewer_loop_log": loop_log,
            },
            metrics={"summary": summary, "per_segment": per_segment},
            warnings=warnings,
            errors=errors,
            execution_time_ms=round(elapsed_ms, 1),
            decision_log=decision_log,
        )
