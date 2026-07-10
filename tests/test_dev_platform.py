"""Tests for Autonomous Development Platform (TZ #10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.architecture_engine import ArchitectureEngine, PROTECTED_MODULES, get_architecture_engine
from core.change_impact import ChangeImpactAnalyzer, get_change_impact_analyzer
from core.code_reviewer import get_code_reviewer
from core.dev_assistant import DevAssistant, assistant_enabled, reset_dev_assistant
from core.development_history import DevelopmentHistoryDB
from core.knowledge_base import KnowledgeBase
from core.refactoring_advisor import get_refactoring_advisor
from core.task_planner import get_task_planner
from core.technical_debt import get_technical_debt_monitor


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DEV_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("VM_KB_DIR", str(tmp_path / "kb"))
    monkeypatch.setenv("VM_DEV_ASSISTANT", "1")
    reset_dev_assistant()
    yield
    reset_dev_assistant()


# ── Project Brain (§1) ───────────────────────────────────────────────


def test_brain_files_exist():
    brain = Path(__file__).parent.parent / ".ai"
    for f in ("PROJECT.md", "CODING_RULES.md", "DECISIONS.md", "CHANGELOG.md"):
        assert (brain / f).is_file(), f"Missing .ai/{f}"


def test_architecture_engine_reads_brain(tmp_path):
    engine = ArchitectureEngine(app_dir=Path(__file__).parent.parent)
    content = engine.read_brain("PROJECT.md")
    assert "VideoMonster" in content
    structure = engine.scan_structure()
    assert structure["total_py_files"] > 20
    assert any(m.startswith("core/") for m in structure["core_modules"])


def test_protected_modules_detected():
    analyzer = ChangeImpactAnalyzer()
    plan = analyzer.analyze(["core/event_bus.py"])
    assert any(v["rule"] == "protected_core" for v in plan.rule_violations)


# ── Change Impact (§3) ───────────────────────────────────────────────


def test_change_impact_plan(tmp_path):
    analyzer = ChangeImpactAnalyzer()
    plan = analyzer.analyze(
        ["core/dev_assistant.py"],
        description="Test change",
        app_dir=Path(__file__).parent.parent,
    )
    assert plan.title
    assert plan.approved is False
    assert any("approval" in s.lower() for s in plan.recommended_steps)
    d = plan.to_dict()
    assert "risks" in d


def test_change_impact_diff():
    diff = "--- a/core/foo.py\n+++ b/core/foo.py\n"
    plan = get_change_impact_analyzer().analyze_diff(
        diff, app_dir=Path(__file__).parent.parent,
    )
    assert "core/foo.py" in plan.files


# ── Code Reviewer (§4) ───────────────────────────────────────────────


def test_code_reviewer_core():
    report = get_code_reviewer().review_files(
        ["core/event_bus.py"],
        app_dir=Path(__file__).parent.parent,
    )
    assert report.files_reviewed == 1
    # Protected module should flag critical
    assert any(f.category == "architecture" for f in report.findings)


# ── Task Planner (§7) ────────────────────────────────────────────────


def test_task_planner_breaks_down():
    planner = get_task_planner()
    plan = planner.plan("Add monitoring for LLM dispatcher and plugin system")
    assert len(plan.steps) >= 2
    assert plan.estimated_complexity in ("low", "medium", "high")
    est = planner.estimate("Simple bugfix in tests")
    assert est["effort_label"] in ("small", "medium", "large")


# ── Technical Debt (§8) ────────────────────────────────────────────


def test_technical_debt_scan():
    summary = get_technical_debt_monitor(
        Path(__file__).parent.parent,
    ).summary()
    assert "total" in summary
    assert "by_category" in summary


# ── Refactoring Advisor (§5) ─────────────────────────────────────────


def test_refactoring_suggestions_no_auto_apply():
    result = get_refactoring_advisor().to_dict(app_dir=Path(__file__).parent.parent)
    assert "developer approval" in result.get("note", "").lower()
    for s in result.get("suggestions", []):
        assert s.get("approved") is False


# ── Knowledge Base (§12) ─────────────────────────────────────────────


def test_knowledge_base_search(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.db")
    results = kb.search("memory")
    assert len(results) >= 1


def test_development_history(tmp_path):
    db = DevelopmentHistoryDB(tmp_path / "history.db")
    cid = db.record_change("Test change", files=["core/foo.py"], reason="test")
    assert cid > 0
    did = db.record_decision("Use plugins", "Plugin-first", rationale="stability")
    assert did > 0
    assert len(db.recent_changes()) == 1


# ── Dev Assistant (§13, §14) ─────────────────────────────────────────


def test_assistant_analyze():
    a = DevAssistant(app_dir=Path(__file__).parent.parent)
    result = a.analyze()
    assert "structure" in result
    assert result["policy"]


def test_assistant_plan():
    a = DevAssistant(app_dir=Path(__file__).parent.parent)
    result = a.plan("Add new TTS plugin for edge voices")
    assert result["requires_approval"] is True
    assert result["plan"]["total_steps"] >= 1


def test_assistant_pre_change():
    a = DevAssistant(app_dir=Path(__file__).parent.parent)
    result = a.pre_change(["core/plugin_manager.py"], description="plugin fix")
    assert result["requires_approval"] is True
    assert "impact" in result


def test_assistant_explain():
    a = DevAssistant(app_dir=Path(__file__).parent.parent)
    result = a.explain("semantic cache")
    assert result["topic"] == "semantic cache"
    assert "knowledge_base" in result


def test_assistant_document(tmp_path):
    engine = ArchitectureEngine(app_dir=tmp_path)
    engine.brain_dir.mkdir(parents=True)
    engine.write_brain("CHANGELOG.md", "# Changelog\n")
    a = DevAssistant(app_dir=tmp_path)
    result = a.document(sync=True)
    assert "updated" in result


def test_assistant_self_diagnose():
    a = DevAssistant(app_dir=Path(__file__).parent.parent)
    result = a.self_diagnose("test-proj", metrics={"segments": 10})
    assert "recommendations" in result
    assert "developer decides" in result["policy"].lower()


def test_flags():
    import os
    os.environ["VM_DEV_ASSISTANT"] = "1"
    assert assistant_enabled() is True
