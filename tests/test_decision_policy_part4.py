"""Master Spec Part 4 — Decision Policy Engine tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _locked_sentence(**kwargs):
    from engines.semantic_v3.types import SemanticSentence

    s = SemanticSentence(
        text=kwargs.get("text", "Hello George."),
        translated_text=kwargs.get("translated_text", "Привіт George."),
        start_ms=kwargs.get("start_ms", 0),
        end_ms=kwargs.get("end_ms", 1000),
        predicted_tts_ms=kwargs.get("predicted_tts_ms", 2000),
        entities=["George"],
        semantic_locked=True,
        lock_status="locked",
        style=kwargs.get("style", "Movie"),
        sentence_confidence=0.95,
    )
    return s


def test_architecture_review_doc_exists():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "ARCHITECTURE_REVIEW_DECISION_POLICY_P301.md").is_file()


def test_costs_and_profiles_from_config_not_code():
    from engines.decision_policy import get_costs, list_profiles, load_policy_config

    cfg = load_policy_config()
    costs = get_costs(cfg)
    assert costs["trim_silence"] == 1
    assert costs["manual_review"] == 100
    assert costs["semantic_rewrite"] == 20
    profiles = list_profiles(cfg)
    for name in (
        "Movie",
        "Series",
        "Anime",
        "Documentary",
        "Interview",
        "Podcast",
        "Kids",
        "Gaming",
        "Lecture",
        "YouTube",
    ):
        assert name in profiles
    # Ensure default file is JSON config
    path = Path(__file__).resolve().parents[1] / "engines" / "decision_policy" / "config" / "default_policy.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "costs" in data and "profiles" in data


def test_multi_strategy_at_least_four_and_picks_best():
    from engines.decision_policy import run_decision_policy
    from engines.decision_policy.cache import clear_decision_cache

    clear_decision_cache()
    s = _locked_sentence(predicted_tts_ms=2500, end_ms=1000)
    graph = run_decision_policy([s], profile="Movie", attach=True)
    assert graph.records
    rec = graph.records[0]
    assert len(rec.candidates) >= 4
    assert rec.accepted is not None
    assert "semantic_rewrite" not in (rec.accepted.steps or [])
    assert s.translated_text == "Привіт George."  # no text mutation
    assert "decision" in (s.context or {})
    assert rec.reason


def test_hard_constraints_reject_rewrite_when_locked():
    from engines.decision_policy.config_loader import get_profile, load_policy_config
    from engines.decision_policy.constraints import hard_constraint_check

    s = _locked_sentence()
    cfg = load_policy_config()
    profile = get_profile("Movie", cfg)
    reasons = hard_constraint_check(s, ["semantic_rewrite", "ready"], profile=profile)
    assert "semantic_lock" in reasons or "policy_disallow_rewrite" in reasons


def test_rollback_picks_next_fitting_strategy():
    from engines.decision_policy.rollback import select_with_rollback
    from engines.decision_policy.types import StrategyCandidate

    a = StrategyCandidate(label="A", steps=["tempo"], decision_score=90, expected_fit=False)
    b = StrategyCandidate(label="B", steps=["borrow_time"], decision_score=80, expected_fit=False)
    c = StrategyCandidate(label="C", steps=["sentence_merge", "tempo"], decision_score=70, expected_fit=True)
    chosen, path = select_with_rollback([a, b, c])
    assert chosen is not None
    assert chosen.label == "C"
    assert "A" in path and "B" in path and "C" in path


def test_decision_cache_deterministic():
    from engines.decision_policy.cache import clear_decision_cache
    from engines.decision_policy import run_decision_policy

    clear_decision_cache()
    s1 = _locked_sentence(predicted_tts_ms=2200, end_ms=1000)
    s2 = _locked_sentence(predicted_tts_ms=2200, end_ms=1000)
    g1 = run_decision_policy([s1], profile="Movie")
    g2 = run_decision_policy([s2], profile="Movie")
    assert g2.records[0].cached is True
    assert g1.records[0].accepted.steps == g2.records[0].accepted.steps


def test_conflict_and_timeline_planner():
    from engines.decision_policy.timeline import detect_conflicts, plan_timeline
    from engines.semantic_v3.types import SemanticSentence

    a = SemanticSentence(text="A.", start_ms=0, end_ms=1000, predicted_tts_ms=1500, scene_uuid="s1")
    b = SemanticSentence(text="B.", start_ms=900, end_ms=2000, predicted_tts_ms=800, scene_uuid="s1")
    conflicts = detect_conflicts([a, b])
    assert any(c["type"] == "overlap" for c in conflicts)
    plan = plan_timeline([a, b])
    assert plan["sentence_count"] == 2
    assert "s1" in plan["scenes"]


def test_isolation_invariant():
    from engines.decision_policy.invariants import assert_decision_policy_isolated

    assert_decision_policy_isolated()


def test_explainability_fields():
    from engines.decision_policy.cache import clear_decision_cache
    from engines.decision_policy import run_decision_policy

    clear_decision_cache()
    s = _locked_sentence(predicted_tts_ms=3000, end_ms=1200, style="Interview")
    graph = run_decision_policy([s], profile="Interview")
    rec = graph.records[0]
    assert rec.accepted and rec.accepted.explanation
    assert rec.reason
    assert rec.confidences.get("sentence", 0) > 0
