"""Tests for Debug/Learning mode across AI Core pipeline."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fresh_task_id(prefix: str = "dbg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TestDebugModeFlag:
    def test_debug_mode_json_enables_flag(self, monkeypatch):
        monkeypatch.delenv("VM_DEBUG_MODE", raising=False)
        from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

        assert IS_DEBUG_LEARNING_MODE() is True

    def test_env_overrides_file(self, monkeypatch):
        monkeypatch.setenv("VM_DEBUG_MODE", "1")
        from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

        assert IS_DEBUG_LEARNING_MODE() is True


class TestRunAgentSafePipeline:
    def test_pipeline_continues_on_agent_failure(self):
        from api.auto_dub_api import _run_agent_safe
        from engines.open_ddf import get_report

        tid = _fresh_task_id("pipe")

        def _boom():
            raise ConnectionError("LLM unavailable")

        def _ok():
            return "step2_ok"

        r1 = _run_agent_safe(tid, "Semantic/v1", _boom, record_success=False)
        r2 = _run_agent_safe(tid, "Timing/v1", _ok, record_success=False)

        assert r1 is None
        assert r2 == "step2_ok"

        report = get_report(tid)
        agents = {a["agent_name"]: a for a in report["agents"]}
        assert agents["Semantic/v1"]["success"] is False
        assert agents["Semantic/v1"]["fallback_used"] is False
        assert "Timing/v1" not in agents

    def test_fallback_recorded_in_ddf(self):
        from api.auto_dub_api import _run_agent_safe
        from engines.open_ddf import get_report

        tid = _fresh_task_id("fb")

        def _boom():
            raise RuntimeError("agent crash")

        def _fallback():
            return ["fallback segment"]

        result = _run_agent_safe(
            tid,
            "Translation/v1",
            _boom,
            fallback_fn=_fallback,
            record_success=False,
        )
        assert result == ["fallback segment"]

        report = get_report(tid)
        entry = next(a for a in report["agents"] if a["agent_name"] == "Translation/v1")
        assert entry["success"] is False
        assert entry["fallback_used"] is True


class TestQualityAgentDebugDowngrade:
    def test_fail_downgraded_to_warning_in_debug_mode(self, tmp_path):
        from engines.ai_core.quality_agent.agent import QualityAgent
        from engines.ai_core.quality_agent.scoring import QualityScores, SegmentAuditResult

        manifest = {
            "project_uuid": str(uuid.uuid4()),
            "source_lang": "en",
            "target_lang": "ru",
        }
        (ROOT / "output" / "manifests" / manifest["project_uuid"]).mkdir(
            parents=True, exist_ok=True
        )

        segs = [
            {
                "index": 0,
                "text": "Hello world.",
                "translated_text": "Привет мир.",
                "semantic_text": "Привет, мир!",
                "timing_text": "Привет, мир!",
                "grammar_text": "Привет, мир!",
                "start": 0,
                "end": 2000,
            }
        ]
        state = {"segments": segs, "grammar_agent_status": "success"}
        agent = QualityAgent(output_dir=tmp_path / "output")

        audit_fail = SegmentAuditResult(
            index=0,
            scores=QualityScores(0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4),
            failure_types=["grammar"],
            reasons=["stiff_copula"],
            critical=False,
        )

        with patch(
            "engines.ai_core.quality_agent.agent.IS_DEBUG_LEARNING_MODE",
            return_value=True,
        ), patch(
            "engines.ai_core.quality_agent.agent.audit_segment",
            return_value=audit_fail,
        ), patch(
            "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
            return_value=(segs[0], "GrammarAgent"),
        ):
            result = agent.run(manifest, state, "t-debug-downgrade")

        assert result.status in ("warning", "success")
        seg = result.updated_state["segments"][0]
        assert seg["quality_decision"] == "WARNING"
        assert seg["quality_passed"] is True

    def test_ddf_records_quality_downgrade(self, tmp_path):
        from engines.ai_core.quality_agent.agent import QualityAgent
        from engines.ai_core.quality_agent.scoring import QualityScores, SegmentAuditResult
        from engines.open_ddf import get_report

        tid = _fresh_task_id("qddf")
        manifest = {
            "project_uuid": str(uuid.uuid4()),
            "source_lang": "en",
            "target_lang": "ru",
        }

        segs = [
            {
                "index": 0,
                "text": "Hi.",
                "translated_text": "Привет.",
                "semantic_text": "Привет.",
                "timing_text": "Привет.",
                "grammar_text": "Привет.",
                "start": 0,
                "end": 1000,
            }
        ]
        state = {"segments": segs, "grammar_agent_status": "success"}
        agent = QualityAgent(output_dir=tmp_path / "output")

        audit_fail = SegmentAuditResult(
            index=0,
            scores=QualityScores(0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3),
            failure_types=["meaning"],
            reasons=["meaning_drift"],
            critical=True,
        )

        with patch(
            "engines.ai_core.quality_agent.agent.IS_DEBUG_LEARNING_MODE",
            return_value=True,
        ), patch(
            "engines.ai_core.quality_agent.agent.audit_segment",
            return_value=audit_fail,
        ), patch(
            "engines.ai_core.quality_agent.smart_router.route_and_fix_segment",
            return_value=(segs[0], "SemanticAgent"),
        ):
            agent.run(manifest, state, tid)

        report = get_report(tid)
        decisions = [a.get("decision") for a in report["agents"]]
        assert "quality_fail_downgraded_to_warning" in decisions


class TestDdfExport:
    def test_consolidated_ddf_saved_to_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr("engines.open_ddf._OUTPUT_DIR", tmp_path)
        from engines.open_ddf import open_ddf, load, save

        tid = _fresh_task_id("export")
        open_ddf.record_agent(tid, "Planner/v3", called=True, success=True)
        open_ddf.record_agent(
            tid,
            "Translation/v1",
            called=True,
            success=False,
            fallback_used=True,
            decision="LLM skipped",
        )
        open_ddf.record_agent(tid, "StudioReady", called=True, success=True)
        path = save(tid)
        assert path is not None
        assert path.name == f"ddf_{tid}.json"

        loaded = load(tid)
        assert loaded is not None
        assert loaded["task_id"] == tid
        assert len(loaded["agents"]) == 3
        assert loaded["summary"]["fallback_used"] >= 1
