"""Tests for Monitoring Center + Analytics + Diagnostics (TZ #8)."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from core.analytics_db import AnalyticsDB
from core.bottleneck_analyzer import BottleneckAnalyzer, get_bottleneck_analyzer
from core.diagnostics import DiagnosticsCenter, get_diagnostics_center
from core.monitoring_center import MonitoringCenter, get_monitor, monitoring_enabled, reset_monitor
from core.report_exporter import export_html, export_json, export_pdf, export_zip, save_report


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_ANALYTICS_DIR", str(tmp_path / "analytics"))
    monkeypatch.setenv("VM_MONITORING", "1")
    reset_monitor()
    yield
    reset_monitor()


# ── Analytics DB (§12) ──────────────────────────────────────────────


def test_analytics_db_save_and_history(tmp_path):
    db = AnalyticsDB(tmp_path / "analytics.db")
    db.save_run("proj-1", duration_s=120.0, speed=2.5, models=["qwen"])
    db.add_timeline("proj-1", "Whisper started", stage="whisper")
    history = db.get_history(project_id="proj-1")
    assert len(history) == 1
    assert history[0]["project_id"] == "proj-1"
    assert history[0]["speed"] == 2.5
    timeline = db.get_timeline("proj-1")
    assert len(timeline) == 1
    assert "Whisper" in timeline[0]["message"]


# ── Bottleneck Analyzer (§10) ───────────────────────────────────────


def test_bottleneck_analyzer_percentages():
    analyzer = BottleneckAnalyzer()
    metrics = {
        "translator": {"busy_ms": 62000, "processed": 10, "errors": 0, "wait_ms": 5000},
        "voice": {"busy_ms": 18000, "processed": 10, "errors": 0},
        "whisper": {"busy_ms": 10000, "processed": 10, "errors": 0},
    }
    report = analyzer.analyze(metrics)
    assert report.primary == "translator"
    assert report.primary_percent > 50
    assert len(report.recommendations) >= 1
    d = report.to_dict()
    assert d["stages"][0]["percent"] >= d["stages"][-1]["percent"]


def test_bottleneck_llm_recommendation():
    analyzer = BottleneckAnalyzer()
    metrics = {
        "translator": {"busy_ms": 90000, "processed": 5, "errors": 0},
        "voice": {"busy_ms": 10000, "processed": 5, "errors": 0},
    }
    report = analyzer.analyze(metrics)
    llm_recs = [r for r in report.recommendations if r.get("category") == "llm"]
    assert len(llm_recs) >= 1


# ── Diagnostics (§9) ─────────────────────────────────────────────────


def test_diagnostics_queue_overflow():
    diag = DiagnosticsCenter()
    state = {
        "queues": {
            "translator": {"current_size": 95, "max_size": 100, "dropped_tasks": 0},
        },
        "pipeline_running": True,
    }
    issues = diag.detect_queue_overflow(state)
    assert any(i.category == "queue_overflow" for i in issues)


def test_diagnostics_memory_leak_trend():
    diag = DiagnosticsCenter()
    state = {
        "resource_history": [
            {"ram_percent": 60 + i * 3} for i in range(8)
        ],
    }
    issues = diag.detect_memory_leak(state)
    assert any(i.category == "memory_leak" for i in issues)


def test_diagnostics_full_scan():
    diag = DiagnosticsCenter()
    report = diag.run_full_scan({
        "queues": {"voice": {"current_size": 99, "max_size": 100}},
        "resource_history": [{"ram_percent": 70 + i} for i in range(6)],
        "statistics": {"total_retries": 20, "chunks_processed": 5},
        "pipeline_running": True,
        "agents": {"agents": {"voice": {"state": "idle"}}},
    })
    assert len(report.issues) >= 1
    assert "issues" in report.to_dict()


def test_ai_diagnostics_report():
    diag = DiagnosticsCenter()
    from core.diagnostics import DiagnosticReport

    dr = DiagnosticReport(issues=[])
    ai = diag.build_ai_report(
        {"primary": "translator", "primary_percent": 62, "recommendations": [
            {"cause": "Translator", "detail": "62%", "action": "Increase queue"},
        ]},
        dr,
    )
    assert len(ai["summary"]) >= 1


# ── Monitoring Center (§1–§17) ───────────────────────────────────────


def test_monitor_timeline_and_progress(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    mon.set_project("test-proj", segments_total=100, chunks_total=10)
    mon.record_event("Whisper started", stage="whisper", event_type="stage")
    mon.update_progress(segments_done=25, chunks_done=2, stage="translator")
    timeline = mon.get_timeline()
    assert len(timeline) >= 1
    dash = mon.get_dashboard()
    assert dash["progress_percent"] == 25.0
    assert dash["project_id"] == "test-proj"


def test_monitor_user_vs_developer_mode(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    mon.set_project("p1", segments_total=10)
    user = mon.get_dashboard(developer=False)
    dev = mon.get_dashboard(developer=True)
    assert "warnings" in user
    assert "orchestrator" in dev
    assert "orchestrator" not in user or user.get("orchestrator") is None


def test_monitor_get_pipeline(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    pipe = mon.get_pipeline()
    assert "stages" in pipe
    assert len(pipe["stages"]) == 8


def test_monitor_get_agents(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    agents = mon.get_agents()
    assert "agents" in agents
    assert "active" in agents
    assert "idle" in agents


def test_monitor_get_resources(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    res = mon.get_resources()
    for key in ("cpu", "gpu", "ram", "disk", "network"):
        assert key in res


def test_monitor_get_models(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    models = mon.get_models()
    assert "models" in models
    assert "active_model" in models


def test_monitor_get_statistics(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    mon.set_project("stats-proj")
    stats = mon.get_statistics()
    assert "project_id" in stats
    assert "total_errors" in stats


def test_monitor_finalize_project(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    mon.set_project("final-proj", segments_total=50)
    mon.record_event("Processing started")
    result = mon.finalize_project("final-proj", errors=[], duration_s=60.0, speed=1.2)
    assert "run_id" in result
    assert result["run_id"] > 0
    assert "ai_report" in result
    history = mon.get_history(project_id="final-proj")
    assert len(history) >= 1


def test_monitor_export_report_bytes(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    mon.set_project("export-proj")
    data = mon._build_full_report()
    assert export_json(data).startswith(b"{")
    assert b"<html" in export_html(data).lower()
    assert export_pdf(data).startswith(b"%PDF")
    zbuf = export_zip(data)
    with zipfile.ZipFile(BytesIO(zbuf)) as zf:
        names = zf.namelist()
        assert "report.json" in names
        assert "report.html" in names
        assert "report.pdf" in names


def test_monitor_save_report(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    data = mon._build_full_report()
    path = save_report(data, tmp_path / "reports", fmt="json")
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert "dashboard" in loaded


def test_monitor_get_history(tmp_path):
    mon = MonitoringCenter(app_dir=tmp_path)
    mon.db.save_run("hist-1", duration_s=30.0)
    history = mon.get_history(limit=10)
    assert len(history) >= 1


def test_get_monitor_singleton(tmp_path):
    m1 = get_monitor(app_dir=tmp_path)
    m2 = get_monitor()
    assert m1 is m2


def test_flags():
    import os

    os.environ["VM_MONITORING"] = "1"
    assert monitoring_enabled() is True
