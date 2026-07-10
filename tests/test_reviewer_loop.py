"""Tests for Reviewer Loop — slot_fit / grammar routing back to agents."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.dub_quality_stabilization import audit_segment_for_reviewer
from engines.ai_core.reviewer_loop import run_reviewer_loop_for_segments


def test_audit_routes_low_slot_fit_to_timing():
    seg = {
        "index": 0,
        "text": "Hello world, this is a longer sentence for timing.",
        "translated_text": "Привіт, світ, це довше речення для таймінгу.",
        "semantic_text": "Привіт, світ, це довше речення для таймінгу.",
        "timing_text": "Привіт, світ, це довше речення для таймінгу зайве.",
        "grammar_text": "Привіт, світ, це довше речення для таймінгу зайве.",
        "slot_fit_score": 0.63,
        "start": 0.0,
        "end": 1.0,
    }
    audit = audit_segment_for_reviewer(
        seg,
        source_lang="en",
        target_lang="uk",
        slot_ms=1000,
    )
    assert audit["pass"] is False
    assert "slot_fit_low" in audit["issues"]
    assert audit["route_to"] == "timing"
    assert audit["slot_fit_score"] == 0.63


def test_audit_routes_low_grammar_score_to_grammar():
    seg = {
        "index": 1,
        "text": "Hello world, how are you today?",
        "translated_text": "Привіт, світ, як у тебе справи сьогодні?",
        "grammar_text": "Привіт, світ, як у тебе справи сьогодні?",
        "grammar_score": 0.78,
        "slot_fit_score": 0.95,
    }
    audit = audit_segment_for_reviewer(
        seg,
        source_lang="en",
        target_lang="uk",
    )
    assert audit["pass"] is False
    assert "grammar_score_low" in audit["issues"]
    assert audit["route_to"] == "grammar"
    assert audit["grammar_score"] == 0.78


def test_reviewer_loop_routes_timing_on_slot_fit(monkeypatch):
    manifest = {"source_lang": "en", "target_lang": "uk", "project_uuid": "p1"}
    seg = {
        "index": 0,
        "text": "Hello world.",
        "translated_text": "Привіт, світ.",
        "semantic_text": "Привіт, світ.",
        "timing_text": "Привіт, світ, дуже довгий текст.",
        "grammar_text": "Привіт, світ, дуже довгий текст.",
        "slot_fit_score": 0.63,
        "start": 0.0,
        "end": 1.0,
    }
    state = {"segments": [seg]}

    calls: list[str] = []

    def fake_rerun(agent_name, segment_index, manifest, state, task_id):
        calls.append(agent_name)
        updated = dict(state["segments"][segment_index])
        if agent_name == "TimingAgent":
            updated["timing_text"] = "Привіт, світ, як у тебе справи сьогодні?"
            updated["slot_fit_score"] = 0.92
        if agent_name == "GrammarAgent":
            updated["grammar_text"] = "Привіт, світ, як у тебе справи сьогодні?"
            updated["grammar_score"] = 0.95
        state["segments"][segment_index] = updated
        return updated

    with patch(
        "engines.ai_core.quality_agent.retry_orchestrator.rerun_agent_for_segment",
        side_effect=fake_rerun,
    ):
        segments, loop_log = run_reviewer_loop_for_segments(
            [seg],
            manifest=manifest,
            state=state,
            task_id="loop-test",
            source_lang="en",
            target_lang="uk",
            max_retries=2,
        )

    assert "TimingAgent" in calls
    assert segments[0].get("reviewer_approved") is True
    assert loop_log[0]["pass"] is True


def test_reviewer_loop_routes_grammar_on_low_score():
    manifest = {"source_lang": "en", "target_lang": "uk"}
    seg = {
        "index": 0,
        "text": "Hello world, how are you today?",
        "translated_text": "Привіт, світ, як у тебе справи сьогодні?",
        "grammar_text": "Привіт, світ, як у тебе справи сьогодні?",
        "grammar_score": 0.78,
        "slot_fit_score": 0.95,
    }
    state = {"segments": [seg]}

    def fake_rerun(agent_name, segment_index, manifest, state, task_id):
        updated = dict(state["segments"][segment_index])
        if agent_name == "GrammarAgent":
            updated["grammar_text"] = "Привіт, світ! Як у тебе справи сьогодні?"
            updated["grammar_score"] = 0.92
        state["segments"][segment_index] = updated
        return updated

    with patch(
        "engines.ai_core.quality_agent.retry_orchestrator.rerun_agent_for_segment",
        side_effect=fake_rerun,
    ):
        segments, loop_log = run_reviewer_loop_for_segments(
            [seg],
            manifest=manifest,
            state=state,
            task_id="grammar-loop",
            source_lang="en",
            target_lang="uk",
            max_retries=2,
        )

    assert segments[0].get("reviewer_approved") is True
    assert any(
        ev.get("routed_agent") == "grammar"
        for ev in (loop_log[0].get("events") or [])
    )
