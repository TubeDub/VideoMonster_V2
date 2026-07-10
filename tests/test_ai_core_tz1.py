"""Tests for TZ #1 — Global Skill, AI Network, unified diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_global_skill_loads_rules():
    from engines.ai_core.global_skill import load_skill, rule_ids, skill_version

    data = load_skill()
    assert data.get("version")
    assert len(rule_ids()) >= 10
    assert skill_version() == "1.0"


def test_augment_system_prompt_injects_preamble():
    from engines.ai_core.global_skill import augment_system_prompt

    out = augment_system_prompt("Be concise.")
    assert "TubeDub" in out or "Global Skill" in out or "quality" in out.lower()
    assert "Be concise." in out


def test_check_agent_result_rejects_english_for_ukrainian():
    from engines.ai_core.global_skill import check_agent_result

    seg = {
        "index": 0,
        "semantic_text": (
            "So George Jr. was a very smart kid, but he also got distracted really easily "
            "and because of that, he really had not pursued anything all that seriously."
        ),
    }
    result = check_agent_result(
        "semantic",
        status="success",
        segments=[seg],
        tgt_lang="uk",
    )
    assert result["approved"] is False
    assert any(v["rule"] == "target_language_only" for v in result["violations"])


def test_ai_network_publish_and_journal():
    from engines.ai_core.ai_network import (
        EVENT_AGENT_STARTED,
        get_network,
        reset_network,
    )

    reset_network("test-run")
    net = get_network("test-run")
    net.publish(EVENT_AGENT_STARTED, "orchestrator", {"agent": "translation"})
    journal = net.journal()
    assert len(journal) == 1
    assert journal[0]["event"] == EVENT_AGENT_STARTED
    reset_network("test-run")


def test_ai_event_log_writes_jsonl(tmp_path, monkeypatch):
    from engines.ai_core import ai_event_log

    monkeypatch.setattr(ai_event_log, "_APP_DIR", tmp_path)
    ai_event_log.agent_started("run1", "Translation", model="llama3.1:8b")
    ai_event_log.agent_finished("run1", "Translation", status="success", ms=1200.0)
    events = ai_event_log.load_ai_events("run1", tmp_path)
    assert len(events) == 2
    assert events[0]["event"] == "Started"
    assert events[1]["event"] == "Finished"


def test_reviewer_gate_publishes(run_id="gate-test"):
    from engines.ai_core.ai_network import get_network, reset_network
    from engines.ai_core.reviewer_gate import review_agent_output

    reset_network(run_id)
    result = review_agent_output(
        run_id,
        "semantic",
        segments=[{"index": 0, "semantic_text": "Привіт світ."}],
        tgt_lang="uk",
    )
    assert result["approved"] is True
    assert len(get_network(run_id).journal()) >= 1
    reset_network(run_id)


def test_development_lifecycle(tmp_path, monkeypatch):
    from engines.ai_core import development_lifecycle as dl

    monkeypatch.setattr(dl, "_APP_DIR", tmp_path)
    dl.record_stage("run-x", dl.STAGE_PLANNING, detail="test")
    data = dl.load_lifecycle("run-x", tmp_path)
    assert data["current_stage"] == dl.STAGE_PLANNING


def test_llm_transport_profiles():
    from engines.llm_providers.transport import list_cloud_profiles, resolve_transport

    profiles = list_cloud_profiles()
    assert len(profiles) >= 2
    tr = resolve_transport()
    assert "kind" in tr


def test_unified_diagnostics_builds_from_artifacts(tmp_path):
    from engines.ai_core.unified_diagnostics import build_unified_diagnostics, save_unified_diagnostics

    run_id = "diag-test"
    ddir = tmp_path / "output" / "diagnostics" / run_id
    ddir.mkdir(parents=True)
    (ddir / "architecture_validation.json").write_text(
        json.dumps(
            {
                "pipeline_status": "completed",
                "active_agents": ["translation", "semantic", "reviewer"],
                "total_execution_time_ms": 5000,
                "agents": [
                    {"name": "translation", "execution_time_ms": 1000, "status": "success"}
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = build_unified_diagnostics(run_id, app_dir=tmp_path)
    assert payload["run_id"] == run_id
    assert payload["pipeline_route"] == ["translation", "semantic", "reviewer"]
    assert "active_model" in payload

    path = save_unified_diagnostics(run_id, app_dir=tmp_path)
    assert path.is_file()
