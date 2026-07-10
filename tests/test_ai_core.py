"""AI Core — single decision-making brain: analysis, strategy, gateway, report."""

from __future__ import annotations

import pytest


# ── Project analysis ───────────────────────────────────────────────────────
def test_analyze_project_basic_profile():
    from engines.ai_core.project_analysis import analyze_project

    src = ["Hello there, my friend!", "How are you doing today?"]
    timing = [
        {"start_ms": 0, "end_ms": 2000},
        {"start_ms": 2500, "end_ms": 5000},
    ]
    profile = analyze_project(
        source_segments=src,
        timing_map=timing,
        src_lang="en",
        tgt_lang="uk",
    )
    assert profile.segment_count == 2
    assert profile.source_lang == "en"
    assert profile.target_lang == "uk"
    assert profile.words_per_second > 0
    assert profile.tempo in {"slow", "medium", "fast"}
    assert profile.content_type  # non-empty


def test_analyze_project_honours_content_hint():
    from engines.ai_core.project_analysis import analyze_project

    profile = analyze_project(
        source_segments=["a b c"],
        timing_map=[{"start_ms": 0, "end_ms": 1000}],
        content_mode_hint="podcast",
    )
    assert profile.content_type == "podcast"


def test_analyze_project_empty_is_safe():
    from engines.ai_core.project_analysis import analyze_project

    profile = analyze_project(source_segments=[], timing_map=[])
    assert profile.segment_count == 0
    assert profile.to_dict()["content_type"]


# ── Strategy ────────────────────────────────────────────────────────────────
def test_build_strategy_variants_within_bounds():
    from engines.ai_core.project_analysis import ProjectProfile
    from engines.ai_core.strategy import build_strategy

    prof = ProjectProfile(content_type="movie", complexity="high", tempo="medium")
    strat = build_strategy(prof, requested_mode="max_quality", llm_available=True)
    assert 5 <= strat.min_variants <= 10
    assert strat.max_variants == 10
    assert strat.speed_mode == "max_quality"
    assert strat.use_llm is True
    assert strat.llm_policy == "always"
    # profile override is consumable by the adaptation engine (bounded rounds)
    ov = strat.adaptation_profile_override()
    assert ov["min_variants"] == strat.min_variants
    assert ov["max_rounds"] == 6
    assert ov["llm_policy"] == "always"


def test_build_strategy_default_is_balanced_not_max_quality():
    """P0 hang fix: auto mode must NOT explode to max_quality on movies."""
    from engines.ai_core.project_analysis import ProjectProfile
    from engines.ai_core.strategy import build_strategy

    strat = build_strategy(
        ProjectProfile(content_type="movie", complexity="high"),
        requested_mode=None,
        llm_available=True,
    )
    assert strat.speed_mode == "balanced"
    assert strat.llm_policy == "problem_only"


def test_build_strategy_fast_disables_llm():
    from engines.ai_core.project_analysis import ProjectProfile
    from engines.ai_core.strategy import build_strategy

    strat = build_strategy(ProjectProfile(), requested_mode="fast", llm_available=True)
    assert strat.llm_policy == "off"
    assert strat.rewrite_required is False


def test_build_strategy_no_llm_disables_rewrite():
    from engines.ai_core.project_analysis import ProjectProfile
    from engines.ai_core.strategy import build_strategy

    strat = build_strategy(ProjectProfile(), llm_available=False)
    assert strat.use_llm is False
    assert strat.rewrite_required is False


def test_build_strategy_respects_requested_mode():
    from engines.ai_core.project_analysis import ProjectProfile
    from engines.ai_core.strategy import build_strategy

    strat = build_strategy(ProjectProfile(), requested_mode="Быстро", llm_available=True)
    assert strat.speed_mode == "fast"


# ── Adaptation profile override plumbing ────────────────────────────────────
def test_profile_override_applies_and_clears():
    import engines.ai_adaptation_engine as eng

    try:
        eng.set_adaptation_profile_override({"min_variants": 8, "max_rounds": 6})
        prof = eng.adaptation_profile()
        assert prof["min_variants"] == 8
        assert prof["max_rounds"] == 6
    finally:
        eng.set_adaptation_profile_override(None)
    assert eng.adaptation_profile()["min_variants"] == eng.MIN_VARIANTS_PER_SEGMENT


# ── Voice director ──────────────────────────────────────────────────────────
def test_decide_voice_energetic_is_expressive():
    from engines.ai_core.project_analysis import ProjectProfile
    from engines.ai_core.voice_director import decide_voice

    prof = ProjectProfile(dominant_emotion="energetic", tempo="fast", content_type="youtube")
    v = decide_voice(prof)
    assert v.emotion == "energetic"
    assert v.intonation == "expressive"
    assert v.rate.endswith("%")


# ── LLM gateway (single entry point) ────────────────────────────────────────
def test_llm_gateway_routes_through_transport(monkeypatch):
    from engines.ai_core import llm_gateway

    seen = {}

    def _fake_chat(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["system"] = kwargs.get("system")
        return "ответ."

    import engines.translation_adapt as ta

    monkeypatch.setattr(ta, "_llm_chat", _fake_chat)
    monkeypatch.setattr(ta, "llm_rephrase_available", lambda: True)
    monkeypatch.setattr(ta, "circuit_open", lambda: False)
    monkeypatch.setattr(llm_gateway, "can_call_llm", lambda *a, **k: (True, ""))
    out = llm_gateway.chat("hello", system="be brief")
    assert out == "ответ."
    assert seen["prompt"] == "hello"
    assert seen["system"] == "be brief"


# ── AI Core facade ──────────────────────────────────────────────────────────
def test_ai_core_analyze_and_plan(monkeypatch):
    from engines.ai_core import get_ai_core, llm_gateway
    import engines.ai_adaptation_engine as eng

    monkeypatch.setattr(llm_gateway, "is_available", lambda: True)
    monkeypatch.setattr(llm_gateway, "active_model", lambda: "test-model")
    monkeypatch.setattr(llm_gateway, "begin_run", lambda *a, **k: None)

    core = get_ai_core("task-aicore-1")
    profile = core.analyze(
        source_segments=["Hello world.", "Another line here."],
        timing_map=[{"start_ms": 0, "end_ms": 2000}, {"start_ms": 2000, "end_ms": 4000}],
        src_lang="en",
        tgt_lang="uk",
        content_mode_hint="movie",
    )
    assert profile.content_type == "movie"
    strat = core.plan(requested_mode="max_quality")
    assert strat.model == "test-model"
    # override actually installed by plan()
    assert eng.adaptation_profile()["min_variants"] == strat.min_variants
    planner = core.to_dict()["agent_report"]["planner"]
    assert planner["called"] is True
    assert planner["status"] == "ok"
    assert planner["output_data"]["strategy"]["model"] == "test-model"
    eng.set_adaptation_profile_override(None)


def test_ai_core_uses_planner_agent_on_live_path(monkeypatch):
    from engines.ai_core import get_ai_core, llm_gateway
    from engines.ai_core.agents.agents_meta import PlannerAgent

    seen = {"profile": 0, "plan": 0}

    def _fake_profile(self, **kwargs):
        seen["profile"] += 1
        return {
            "source_lang": "en",
            "target_lang": "uk",
            "content_type": "movie",
            "genre": "narrative",
            "speech_style": "neutral",
            "dominant_emotion": "neutral",
            "tempo": "medium",
            "words_per_second": 2.0,
            "dialogue_density": 0.5,
            "speaker_count": 1,
            "complexity": "medium",
            "complexity_score": 0.5,
            "segment_count": 1,
            "total_duration_ms": 1000,
            "avg_segment_ms": 1000.0,
            "avg_gap_ms": 0.0,
            "notes": [],
        }

    def _fake_plan(self, profile, *, requested_mode=None):
        seen["plan"] += 1
        return {
            "speed_mode": "balanced",
            "per_segment_budget_s": 0.0,
            "project_budget_s": 0.0,
            "use_llm": True,
            "llm_policy": "problem_only",
            "min_variants": 5,
            "max_variants": 8,
            "variants_per_round": 3,
            "min_rounds": 2,
            "max_rounds": 4,
            "rewrite_required": True,
            "max_parallel_segments": 0,
            "checks": ["meaning"],
            "predict_before_tts": True,
            "protect_entities": True,
            "block_hallucinations": True,
            "forbid_truncation": True,
            "voice_emotion": "neutral",
            "voice_tempo": "medium",
            "preserve_music": True,
            "ducking_enabled": True,
            "model": "planner-model",
            "rationale": ["planner"],
        }

    monkeypatch.setattr(llm_gateway, "begin_run", lambda *a, **k: None)
    monkeypatch.setattr(PlannerAgent, "analyze_profile", _fake_profile)
    monkeypatch.setattr(PlannerAgent, "build_plan", _fake_plan)

    core = get_ai_core("task-aicore-planner")
    core.analyze(
        source_segments=["Hello world."],
        timing_map=[{"start_ms": 0, "end_ms": 1000}],
        src_lang="en",
        tgt_lang="uk",
        content_mode_hint="movie",
    )
    core.plan(requested_mode="balanced")

    assert seen["profile"] == 1
    assert seen["plan"] == 1
    assert core.to_dict()["agent_report"]["planner"]["called"] is True


def test_ai_core_uses_mix_agent_on_live_path(monkeypatch):
    from engines.ai_core import get_ai_core, release_ai_core
    from engines.ai_core import llm_gateway

    monkeypatch.setattr(llm_gateway, "begin_run", lambda *a, **k: None)
    core = get_ai_core("task-aicore-mix")
    try:
        core.analyze(
            source_segments=["Hello world."],
            timing_map=[{"start_ms": 0, "end_ms": 1000}],
            src_lang="en",
            tgt_lang="uk",
            content_mode_hint="movie",
        )
        core.plan(requested_mode="balanced")
        mix_plan = core.decide_mix_plan()
        rep = core.to_dict()["agent_report"]["mix"]
    finally:
        release_ai_core("task-aicore-mix")

    assert mix_plan["preserve_music"] is True
    assert mix_plan["output"] == "final_mp4"
    assert rep["called"] is True
    assert rep["status"] == "ok"
    assert rep["output_data"]["mix_plan"]["output"] == "final_mp4"


# ── OpenDDF AI Core report ──────────────────────────────────────────────────
def test_build_ai_core_report_shapes():
    from engines.ai_core.report import build_ai_core_report

    task_info = {
        "ai_core": {
            "profile": {"content_type": "movie", "tempo": "medium", "genre": "narrative"},
            "strategy": {"speed_mode": "max_quality", "min_variants": 10, "max_variants": 10, "model": "m"},
            "agent_report": {
                "planner": {
                    "called": True,
                    "execution_time_ms": 12.3,
                    "input_data": {"source_count": 1},
                    "output_data": {"strategy": {"speed_mode": "max_quality"}},
                    "decision_taken": "mode:max_quality",
                    "errors": [],
                    "rerun": False,
                    "status": "ok",
                },
                "mix": {
                    "called": True,
                    "execution_time_ms": 2.1,
                    "input_data": {"strategy": {"preserve_music": True}},
                    "output_data": {"mix_plan": {"preserve_music": True, "ducking_enabled": True, "output": "final_mp4"}},
                    "decision_taken": "preserve_music:True;ducking:True;output:final_mp4",
                    "errors": [],
                    "rerun": False,
                    "status": "ok",
                }
            },
        },
        "timing_aware_records": [
            {
                "index": 0,
                "predicted_ms_before": 5000,
                "predicted_ms_after": 4200,
                "slot_ms": 4300,
                "llm_called": True,
                "ai_adaptation_trace": {
                    "chosen_reason": "best_variant",
                    "llm_calls": 3,
                    "llm_total_ms": 1200.0,
                    "iterations": 2,
                    "slot_fit_score": 0.92,
                    "meaning_score": 0.95,
                    "naturalness_score": 0.9,
                    "variants": [
                        {"strategy": "shorten", "selected": True, "scores": {"total": 0.9}},
                        {"strategy": "restructure", "selected": False, "rejected_reason": "timing", "scores": {"total": 0.6}},
                    ],
                },
            }
        ],
    }
    report = build_ai_core_report(task_info)
    assert report["summary"]["segment_count"] == 1
    assert report["summary"]["total_variants"] == 2
    assert report["summary"]["total_llm_calls"] == 3
    assert report["segments"][0]["strategy"] == "shorten"
    assert report["segments"][0]["winner_reason"] == "best_variant"
    assert report["ai_core_report_ru"]["Использованная модель"] == "m"
    assert report["ai_agent_report"]["agents"]["planner"]["called"] is True
    assert report["ai_agent_report"]["agents"]["planner"]["decision_taken"] == "mode:max_quality"
    assert report["ai_agent_report"]["agents"]["mix"]["called"] is True
    assert report["ai_agent_report"]["agents"]["mix"]["output_data"]["mix_plan"]["output"] == "final_mp4"


def test_build_ai_core_report_empty_is_safe():
    from engines.ai_core.report import build_ai_core_report

    report = build_ai_core_report({})
    assert report["summary"]["segment_count"] == 0
    assert report["summary"]["enabled"] is False


# ── Decision Engine (Task 3/10): cheapest strategy that works ───────────────
def test_decision_engine_balanced_skips_llm_when_prep_fits(monkeypatch):
    """A mildly-long segment whose rule prep fits must NOT call the LLM."""
    import engines.ai_adaptation_engine as eng

    # Force the balanced policy and make the LLM look available so we can prove
    # it is NOT used because the rule prep already fits.
    eng.set_adaptation_profile_override({"llm_policy": "problem_only"})

    calls = {"n": 0}

    def _boom_chat(*a, **k):
        calls["n"] += 1
        return "should-not-be-called"

    monkeypatch.setattr("engines.translation_adapt.llm_rephrase_available", lambda: True)
    monkeypatch.setattr("engines.translation_adapt._llm_chat", _boom_chat)

    try:
        # Give a big slot so rule prep (or even the original) fits easily.
        result = eng.adapt_segment_ai(
            "Це трохи довше речення для перевірки.",
            source_hint="This is a slightly longer sentence for checking.",
            slot_ms=20000,
            tgt_lang="uk",
            index=0,
        )
    finally:
        eng.set_adaptation_profile_override(None)

    assert result.llm_called is False
    assert result.requires_llm_adaptation is False
    assert result.trace.strategy_class in ("none", "rule_rewrite")


def test_decision_engine_fast_never_calls_llm(monkeypatch):
    import engines.ai_adaptation_engine as eng

    eng.set_adaptation_profile_override({"llm_policy": "off"})
    called = {"n": 0}
    monkeypatch.setattr("engines.translation_adapt.llm_rephrase_available", lambda: True)
    monkeypatch.setattr(
        "engines.translation_adapt._llm_chat",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "x",
    )
    try:
        result = eng.adapt_segment_ai(
            "Дуже довге речення яке точно не поміститься у короткий слот озвучування взагалі ніяк.",
            source_hint="A very long line that will not fit at all in a tiny slot.",
            slot_ms=300,
            tgt_lang="uk",
            index=1,
        )
    finally:
        eng.set_adaptation_profile_override(None)
    assert result.llm_called is False
    assert result.requires_llm_adaptation is False
    assert result.trace.strategy_class == "rule_rewrite"
    assert called["n"] == 0


def test_trace_has_timeline_fields():
    import engines.ai_adaptation_engine as eng

    result = eng.adapt_segment_ai(
        "Короткий текст.",
        source_hint="Short text.",
        slot_ms=5000,
        tgt_lang="uk",
        index=0,
    )
    d = result.trace.to_dict()
    assert "started_at" in d and "ended_at" in d and "total_ms" in d
    assert d["strategy_class"] in ("none", "rule_rewrite", "full_llm")


# ── Timeline + performance report ───────────────────────────────────────────
def _timeline_task_info():
    return {
        "ai_core": {"profile": {"content_type": "movie"}, "strategy": {"speed_mode": "balanced", "model": "m"}},
        "timing_aware_records": [
            {
                "index": 0, "predicted_ms_after": 4000, "slot_ms": 4200, "llm_called": False,
                "ai_adaptation_trace": {
                    "strategy_class": "rule_rewrite", "started_at": 100.0, "ended_at": 100.2,
                    "total_ms": 200.0, "attempts": 0, "iterations": 0, "slot_fit_score": 0.9,
                    "llm_calls": 0, "llm_total_ms": 0.0, "variants": [],
                },
            },
            {
                "index": 1, "predicted_ms_after": 3900, "slot_ms": 4000, "llm_called": True,
                "ai_adaptation_trace": {
                    "strategy_class": "full_llm", "started_at": 100.2, "ended_at": 103.2,
                    "total_ms": 3000.0, "attempts": 2, "iterations": 2, "slot_fit_score": 0.85,
                    "llm_calls": 4, "llm_total_ms": 2800.0,
                    "variants": [{"strategy": "shorten", "selected": True, "text": "ok", "scores": {"total": 0.9}}],
                },
            },
        ],
    }


def test_build_ai_core_timeline():
    from engines.ai_core.report import build_ai_core_timeline

    tl = build_ai_core_timeline(_timeline_task_info())
    assert len(tl) == 2
    assert tl[0]["strategy"] == "rule_rewrite"
    assert tl[1]["attempts"] == 2
    assert tl[1]["chosen_variant"] == "ok"


def test_build_ai_performance_report():
    from engines.ai_core.report import build_ai_performance_report

    perf = build_ai_performance_report(_timeline_task_info())
    assert perf["segment_count"] == 2
    assert perf["total_llm_calls"] == 4
    assert perf["segments_needed_llm"] == 1
    assert perf["fast_path_pct"] == 50.0
    assert perf["full_llm_pct"] == 50.0
    assert perf["total_retries"] == 1
