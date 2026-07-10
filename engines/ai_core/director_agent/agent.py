"""TubeDub Director Agent v1.0 — READ ONLY creative brief coordinator."""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.director_agent.brief_validator import (
    merge_rule_and_llm,
    repair_brief,
    validate_all_briefs,
)
from engines.ai_core.director_agent.context_window import build_context_window
from engines.ai_core.director_agent.creative_brief import CreativeBrief
from engines.ai_core.director_agent.llm_analyzer import analyze_segment_llm
from engines.ai_core.director_agent.rule_analyzer import analyze_segment_rules
from engines.ai_core.translation_agent.agent import load_manifest
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.director_agent")

_APP_DIR = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _APP_DIR / "output"
_MANIFESTS_DIR = _OUTPUT_DIR / "manifests"

_TEXT_FIELDS = frozenset(
    {"text", "translated_text", "semantic_text", "timing_text", "grammar_text"}
)


class DirectorAgent:
    """Decision coordinator — writes segments[].creative_brief only (READ ONLY)."""

    VERSION = "1.0"

    def __init__(self, output_dir: Path | None = None, *, context_window: int = 2):
        self.output_dir = output_dir or _OUTPUT_DIR
        self._manifests_dir = self.output_dir / "manifests"
        self.context_window = context_window

    def _check_gatekeeper(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        warnings: list[str] = []
        if not manifest.get("project_uuid"):
            return False, ["planner_not_complete:missing_project_uuid"]
        segments = state.get("segments") or []
        if not segments:
            return False, ["director_no_segments"]
        has_text = any(str(s.get("text") or "").strip() for s in segments)
        if not has_text:
            warnings.append("director_no_source_text")
        return True, warnings

    def _save_report(
        self,
        manifest: dict[str, Any],
        task_id: str,
        briefs: list[dict[str, Any]],
        stats: dict[str, Any],
        decision_log: list[str],
        elapsed_ms: float,
        status: str,
        errors: list[str],
        warnings: list[str],
    ) -> Path:
        project_uuid = manifest.get("project_uuid") or "unknown"
        report_dir = self._manifests_dir / project_uuid
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "director_report.json"

        report = {
            "director_agent_version": self.VERSION,
            "openddf_agent": "Director/v1",
            "project_uuid": project_uuid,
            "task_id": task_id,
            "status": status,
            "segment_count": len(briefs),
            "llm_used_count": stats.get("llm_used_count", 0),
            "rule_only_count": stats.get("rule_only_count", 0),
            "llm_skipped": stats.get("llm_skipped", False),
            "validation_ok": stats.get("validation_ok", True),
            "warnings": warnings,
            "errors": errors,
            "execution_time_ms": round(elapsed_ms, 1),
            "decision_log": decision_log,
            "per_segment": briefs,
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
        """Produce creative_brief per segment — never mutates text/timing/media."""
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        decision_log: list[str] = []

        gate_ok, gate_msgs = self._check_gatekeeper(manifest, state)
        if not gate_ok:
            errors.extend(gate_msgs)
            elapsed = (time.perf_counter() - t0) * 1000
            debug_mode = IS_DEBUG_LEARNING_MODE()
            if debug_mode:
                return AgentExecutionResult(
                    status="warning",
                    updated_state={"segments": state.get("segments") or []},
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
        source_lang = normalize_lang(manifest.get("source_lang") or "auto")
        target_lang = normalize_lang(manifest.get("target_lang") or "ru")
        language = source_lang if source_lang != "auto" else target_lang

        stats: dict[str, Any] = {
            "llm_used_count": 0,
            "rule_only_count": 0,
            "llm_skipped": False,
            "validation_ok": True,
        }
        briefs: list[dict[str, Any]] = []
        llm_calls = 0

        for i, seg in enumerate(segments):
            idx = int(seg.get("index", i))
            snapshot_text = {k: seg.get(k) for k in _TEXT_FIELDS if k in seg}

            ctx = build_context_window(segments, i, window=self.context_window)
            slot_ms = max(1, int(seg.get("end", 0) or 0) - int(seg.get("start", 0) or 0))
            if slot_ms <= 1:
                start = seg.get("start")
                end = seg.get("end")
                if start is not None and end is not None:
                    slot_ms = max(1, int(end) - int(start))
                else:
                    slot_ms = max(1, int(seg.get("slot_ms") or 3000))
            ctx["slot_ms"] = slot_ms

            rule_fields = analyze_segment_rules(
                seg,
                segments=segments,
                index=i,
                language=language,
                window=self.context_window,
            )

            try:
                llm_fields, llm_used = analyze_segment_llm(
                    seg,
                    context=ctx,
                    language=language,
                    task_id=task_id,
                    segment_idx=idx,
                )
            except Exception as exc:
                llm_fields, llm_used = None, False
                decision_log.append(f"segment_{idx}:llm_error={exc}")
                stats["llm_skipped"] = True
            if llm_used:
                llm_calls += 1
                if llm_fields:
                    stats["llm_used_count"] += 1
                else:
                    stats["llm_skipped"] = True
                    decision_log.append(f"segment_{idx}:LLM skipped")
            else:
                stats["rule_only_count"] += 1
                if not llm_fields:
                    rule_fields.setdefault("decision_reasons", []).append("LLM skipped")

            merged = merge_rule_and_llm(rule_fields, llm_fields)
            merged["segment_id"] = idx
            merged["speaker_id"] = str(seg.get("speaker") or seg.get("speaker_id") or "default")
            merged["language"] = language
            merged["maximum_duration_ms"] = slot_ms
            if not merged.get("preferred_duration_ms"):
                merged["preferred_duration_ms"] = slot_ms

            repaired = repair_brief(merged)
            brief = CreativeBrief.from_dict(repaired)
            brief_dict = brief.to_dict()
            seg["creative_brief"] = brief_dict
            briefs.append(brief_dict)

            for key, val in snapshot_text.items():
                if seg.get(key) != val:
                    seg[key] = val
                    warnings.append(f"segment_{idx}:text_guard_restored_{key}")

            decision_log.append(
                f"segment_{idx}:emotion={brief.emotion} style={brief.speech_style} "
                f"speed={brief.speaking_speed} llm={bool(llm_fields)}"
            )

        validation_ok, validation_issues = validate_all_briefs(briefs)
        stats["validation_ok"] = validation_ok
        if validation_issues:
            warnings.extend(validation_issues[:20])

        for i, brief_dict in enumerate(briefs):
            if i < len(segments):
                segments[i]["creative_brief"] = brief_dict

        elapsed_ms = (time.perf_counter() - t0) * 1000
        status = "error" if errors else ("warning" if warnings else "success")

        report_path = self._save_report(
            manifest,
            task_id,
            briefs,
            stats,
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
                "Director/v1",
                called=True,
                success=status != "error",
                decision="creative_briefs_ready",
                execution_time_ms=elapsed_ms,
                llm_calls=llm_calls,
                input_metrics={"segment_count": len(segments)},
                output_metrics={
                    "brief_count": len(briefs),
                    "llm_used_count": stats["llm_used_count"],
                    "rule_only_count": stats["rule_only_count"],
                },
            )
        except Exception:
            pass

        return AgentExecutionResult(
            status=status,
            updated_state={
                "segments": segments,
                "creative_briefs": briefs,
                "director_report_path": str(report_path),
                "director_agent_status": status,
            },
            metrics={
                "segment_count": len(segments),
                "brief_count": len(briefs),
                "llm_used_count": stats["llm_used_count"],
                "rule_only_count": stats["rule_only_count"],
                "llm_calls": llm_calls,
                "validation_ok": validation_ok,
            },
            warnings=warnings,
            errors=errors,
            execution_time_ms=round(elapsed_ms, 1),
            decision_log=decision_log,
        )


__all__ = ["DirectorAgent"]
