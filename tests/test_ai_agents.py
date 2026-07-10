"""AI Core 3.0 — multi-agent coordinator tests (LLM fully mocked).

Covers: each agent's single responsibility, cheap-vs-LLM path, per-agent cache,
Quality routing to ONLY the responsible agent, the no-empty/no-English/no-hang
safety net, and the OpenDDF "AI Agent Timeline" shape.
"""

from __future__ import annotations

import os
import unittest.mock as mock
import uuid

import pytest

import engines.translation_adapt as ta  # noqa: E402
from engines.ai_core.agents import (  # noqa: E402
    AgentCoordinator,
    EntityAgent,
    GrammarAgent,
    QualityAgent,
    SegmentContext,
    SemanticAgent,
    TimingAgent,
    TranslationAgent,
)

UK_SHORT = "Це коротке речення."
UK_LONG = (
    "Це дуже довге українське речення для перевірки адаптації, "
    "яке напевно не поміститься у відведений короткий слот озвучування зовсім."
)


def _ctx(**kw) -> SegmentContext:
    base = dict(
        index=0,
        source_text="A short English source line.",
        raw_translation=UK_SHORT,
        text=UK_SHORT,
        slot_ms=6000,
        src_lang="en",
        tgt_lang="uk",
        strategy={"use_llm": True, "speed_mode": "balanced", "llm_policy": "problem_only"},
    )
    base.update(kw)
    return SegmentContext(**base)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    # Deterministic + non-polluting: disable LLM autodiscovery and isolate the
    # persistent agent cache to a temp dir for every test (auto-undone).
    monkeypatch.setenv("VM_LLM_AUTODISCOVER", "0")
    monkeypatch.setenv("VM_LLM_CACHE_DIR", str(tmp_path))
    ta.reset_llm_budget()
    yield
    ta.reset_llm_budget()


# ── Translation Agent ────────────────────────────────────────────────────────
def test_translation_agent_passthrough_no_llm():
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return "should-not-be-called"

    with mock.patch.object(ta, "_llm_chat", _fake):
        res = TranslationAgent().run(_ctx())
    assert res.text == UK_SHORT
    assert res.changed is False
    assert calls["n"] == 0  # cheap path: never touches the LLM


def test_translation_agent_anchors_raw_mt_when_incoming_differs():
    ctx = _ctx(
        text="Фінальний відредагований текст.",
        raw_translation="Сирий буквальний переклад.",
    )
    res = TranslationAgent().run(ctx)
    assert res.text == "Сирий буквальний переклад."
    assert res.changed is True
    assert res.reason == "anchor_raw_mt"
    assert res.diagnostics["input_data"]["incoming_text"] == "Фінальний відредагований текст."
    assert res.diagnostics["output_data"]["text"] == "Сирий буквальний переклад."


def test_translation_agent_never_emits_source_language_raw():
    ctx = _ctx(
        source_text="Hello world.",
        text="Привіт, світе.",
        raw_translation="Hello world.",
    )
    res = TranslationAgent().run(ctx)
    assert res.text == "Привіт, світе."
    assert res.reason == "raw_mt_lang_mismatch_keep_current"


def test_translation_agent_retranslate_uses_llm_when_routed_back():
    ctx = _ctx(text="wrong", raw_translation="wrong",
               source_text=f"Unique source {uuid.uuid4()}.")
    ctx.diagnostics["force_retranslate"] = True
    with mock.patch.object(ta, "llm_rephrase_available", lambda: True), \
         mock.patch.object(ta, "_llm_chat", lambda *a, **k: "Виправлений переклад."):
        res = TranslationAgent().run(ctx)
    assert res.used_llm is True
    assert res.text == "Виправлений переклад."


def test_live_ai_core_path_uses_translation_agent_raw_mt(monkeypatch):
    from engines.ai_core import get_ai_core, release_ai_core
    import engines.ai_core.core as core_mod

    monkeypatch.setattr(core_mod, "_agents_enabled", lambda: True)
    core = get_ai_core("task-aicore-translation-live")
    try:
        core.analyze(
            source_segments=[""],
            timing_map=[{"start_ms": 0, "end_ms": 6000}],
            src_lang="en",
            tgt_lang="uk",
            content_mode_hint="movie",
        )
        core.plan(requested_mode="balanced")
        texts, records = core.adapt_segments(
            ["Фінальний відредагований текст."],
            [{"start_ms": 0, "end_ms": 6000}],
            [""],
            src_lang="en",
            tgt_lang="uk",
            raw_mt_segments=["Сирий буквальний переклад."],
        )
    finally:
        release_ai_core("task-aicore-translation-live")

    assert texts[0] == "Сирий буквальний переклад."
    chain = records[0].ai_adaptation_trace["agent_timeline"]
    first = chain[0]
    assert first["agent"] == "translation"
    assert first["input_data"]["incoming_text"] == "Фінальний відредагований текст."
    assert first["output_data"]["text"] == "Сирий буквальний переклад."


def test_live_ai_core_path_uses_semantic_agent_for_semantic_variant(monkeypatch):
    from engines.ai_core import get_ai_core, release_ai_core
    import engines.ai_core.core as core_mod

    monkeypatch.setattr(core_mod, "_agents_enabled", lambda: True)
    core = get_ai_core("task-aicore-semantic-live")
    try:
        core.analyze(
            source_segments=["This makes sense."],
            timing_map=[{"start_ms": 0, "end_ms": 6000}],
            src_lang="en",
            tgt_lang="uk",
            content_mode_hint="movie",
        )
        core.plan(requested_mode="balanced")
        texts, records = core.adapt_segments(
            ["Це робить сенс."],
            [{"start_ms": 0, "end_ms": 6000}],
            ["This makes sense."],
            src_lang="en",
            tgt_lang="uk",
            raw_mt_segments=["Це робить сенс."],
        )
    finally:
        release_ai_core("task-aicore-semantic-live")

    assert texts[0] == "Це має сенс."
    chain = records[0].ai_adaptation_trace["agent_timeline"]
    semantic = [step for step in chain if step["agent"] == "semantic"][0]
    assert semantic["input_data"]["incoming_text"] == "Це робить сенс."
    assert semantic["output_data"]["text"] == "Це має сенс."


# ── Semantic Agent ───────────────────────────────────────────────────────────
def test_semantic_agent_confirms_when_meaning_preserved():
    res = SemanticAgent().run(_ctx())
    assert res.ok is True
    assert res.route_back_to is None


def test_semantic_agent_generates_and_chooses_semantic_variant():
    ctx = _ctx(
        source_text="This makes sense.",
        raw_translation="Це робить сенс.",
        text="Це робить сенс.",
    )
    res = SemanticAgent().run(ctx)
    assert res.ok is True
    assert res.changed is True
    assert res.text == "Це має сенс."
    assert "semantic_variant" in res.reason
    labels = {v["label"] for v in res.diagnostics["variants"]}
    assert "original" in labels
    assert "semantic_polish" in labels


def test_semantic_agent_removes_duplicate_meaning_without_timing_logic():
    ctx = _ctx(
        source_text="George drove home.",
        raw_translation="Джордж поїхав додому. Джордж поїхав додому.",
        text="Джордж поїхав додому. Джордж поїхав додому.",
        slot_ms=1,
    )
    res = SemanticAgent().run(ctx)
    assert res.ok is True
    assert res.changed is True
    assert res.text == "Джордж поїхав додому."
    assert "slot_ms" not in (res.diagnostics.get("input_data") or {})


def test_semantic_agent_routes_back_to_translation_on_loss():
    ctx = _ctx(source_text="The president visited Kyiv.", text="", raw_translation="")
    res = SemanticAgent().run(ctx)
    assert res.ok is False
    assert res.route_back_to == "translation"


# ── Entity Agent ─────────────────────────────────────────────────────────────
def test_entity_agent_flags_missing_number():
    ctx = _ctx(source_text="We sold 250 units.", text="Ми продали товар.",
               raw_translation="Ми продали товар.")
    res = EntityAgent().run(ctx)
    assert res.ok is False
    assert res.route_back_to == "translation"
    assert "250" in str(res.diagnostics.get("missing_numbers"))


def test_entity_agent_ok_when_number_present():
    ctx = _ctx(source_text="We sold 250 units.", text="Ми продали 250 одиниць.",
               raw_translation="Ми продали 250 одиниць.")
    res = EntityAgent().run(ctx)
    assert res.ok is True


# ── Timing Agent ─────────────────────────────────────────────────────────────
def test_timing_agent_skips_when_fits():
    # Fits comfortably and no source to expand against → no timing work.
    ctx = _ctx(text=UK_SHORT, slot_ms=8000, source_text="")
    assert TimingAgent().needed(ctx) is False


def test_timing_agent_needed_when_overflow():
    ctx = _ctx(text=UK_LONG, slot_ms=1200)
    assert TimingAgent().needed(ctx) is True


def test_timing_agent_finalizes_unresolved_handoff(monkeypatch):
    from engines.timing_aware_translation import TimingAwareRecord

    rec = TimingAwareRecord(
        index=0,
        text_before=UK_LONG,
        text_after=UK_LONG,
        adapted=False,
        slot_ms=1200,
        predicted_ms_before=9000,
        predicted_ms_after=9000,
        reason="requires_llm_adaptation",
        requires_llm_adaptation=True,
        llm_called=False,
        ai_adaptation_trace={},
    )
    monkeypatch.setattr(
        "engines.timing_aware_translation.adapt_segment_to_slot",
        lambda *a, **k: (UK_LONG, rec),
    )
    agent = TimingAgent()
    ctx = _ctx(text=UK_LONG, raw_translation=UK_LONG, slot_ms=1200)
    res = agent.run(ctx)
    assert res.reason == "video_adapt_required"
    assert ctx.timing_record.requires_llm_adaptation is False
    assert ctx.timing_record.ai_adaptation_trace["timing_handoff_strategy"] == "video_adapt"


def test_timing_agent_cache_hit_second_run():
    agent = TimingAgent()
    unique = f"{UK_LONG} Унікальний маркер {uuid.uuid4().hex}."
    ctx1 = _ctx(text=unique, slot_ms=1500)
    ctx2 = _ctx(text=unique, slot_ms=1500)
    with mock.patch.object(ta, "llm_rephrase_available", lambda: False):
        r1 = agent.run(ctx1)
        r2 = agent.run(ctx2)
    assert r1.cache_hit is False
    assert r2.cache_hit is True


# ── Grammar Agent ────────────────────────────────────────────────────────────
def test_grammar_agent_skips_when_perfect():
    ctx = _ctx(text=UK_SHORT)
    # A well-formed sentence ending in "." needs no grammar work.
    assert GrammarAgent().needed(ctx) is False


def test_grammar_agent_detects_live_bad_mt_phrase():
    bad = "Але коли Джордж їхав за кермом, він відчувати, що він справді боявся потрапити туди."
    ctx = _ctx(text=bad, raw_translation=bad)
    res = GrammarAgent().run(ctx)
    assert GrammarAgent().needed(_ctx(text=bad, raw_translation=bad)) is True
    assert res.changed is True
    assert "він відчував, що" in res.text


def test_grammar_agent_cheap_path_no_llm():
    # Extra spaces but otherwise a complete sentence → rule polish fixes it and
    # the result is valid, so the LLM path is never taken.
    ctx = _ctx(text="Це   погано   форматований   текст.")
    from engines.ai_core import llm_gateway
    with mock.patch.object(llm_gateway, "chat", side_effect=AssertionError("LLM should not run")):
        res = GrammarAgent().run(ctx)
    assert "  " not in res.text


def test_grammar_agent_llm_rejected_if_it_reopens_timing():
    bad = "Але коли він йшов туди, чоловік зі смілостями підійшов до нього."
    ctx = _ctx(text=bad, raw_translation=bad, slot_ms=2400)
    from engines.ai_core import llm_gateway
    with mock.patch.object(llm_gateway, "chat", return_value=(
        "Але коли він йшов туди, дуже сміливий і надзвичайно харизматичний чоловік "
        "із виразними сміливими рисами обличчя повільно підійшов до нього."
    )):
        res = GrammarAgent().run(ctx)
    assert res.text == bad
    assert res.changed is False


def test_live_ai_core_path_uses_grammar_agent_for_naturalness(monkeypatch):
    from engines.ai_core import get_ai_core, release_ai_core
    import engines.ai_core.core as core_mod
    from engines.ai_adaptation_engine import set_adaptation_profile_override

    bad = "Тепер Джордж-молодший підійшов до подіуму, щоб зробити кілька фотографій переможного їзда."
    monkeypatch.setattr(core_mod, "_agents_enabled", lambda: True)
    core = get_ai_core("task-aicore-grammar-live")
    try:
        core.analyze(
            source_segments=["Now George Junior walked up to the podium to take pictures of the winning drive."],
            timing_map=[{"start": 0, "end": 12000}],
            src_lang="en",
            tgt_lang="uk",
            content_mode_hint="movie",
        )
        core.plan(requested_mode="balanced")
        texts, records = core.adapt_segments(
            [bad],
            [{"start": 0, "end": 12000}],
            ["Now George Junior walked up to the podium to take pictures of the winning drive."],
            src_lang="en",
            tgt_lang="uk",
            raw_mt_segments=[bad],
        )
    finally:
        set_adaptation_profile_override(None)
        release_ai_core("task-aicore-grammar-live")

    chain = records[0].ai_adaptation_trace["agent_timeline"]
    timing = [step for step in chain if step["agent"] == "timing"][0]
    grammar = [step for step in chain if step["agent"] == "grammar"][0]
    assert timing["reason"] == "not_needed"
    assert grammar["reason"] != "not_needed"
    assert grammar["changed"] is True
    assert "переможного заїзду" in texts[0]


# ── Quality Agent routing ────────────────────────────────────────────────────
def test_quality_routes_timing_overflow_to_timing():
    # Meaning preserved (text == raw translation, no source token loss) so the
    # only failing dimension is the timing overflow → route to Timing only.
    ctx = _ctx(text=UK_LONG, raw_translation=UK_LONG, source_text="", slot_ms=800)
    res = QualityAgent().run(ctx)
    assert res.ok is False
    assert res.route_back_to == "timing"


def test_quality_routes_bad_mt_to_grammar():
    bad = "Тепер Джордж-молодший підійшов до подіуму, щоб зробити кілька фотографій переможного їзда."
    ctx = _ctx(text=bad, raw_translation=bad, slot_ms=12000)
    res = QualityAgent().run(ctx)
    assert res.ok is False
    assert res.route_back_to == "grammar"
    assert "bad_mt:" in res.reason


def test_quality_passes_good_segment():
    ctx = _ctx(text=UK_SHORT, slot_ms=8000)
    res = QualityAgent().run(ctx)
    assert res.ok is True
    assert res.route_back_to is None


# ── Coordinator integration ──────────────────────────────────────────────────
def _coord() -> AgentCoordinator:
    strategy = {"use_llm": True, "speed_mode": "balanced", "llm_policy": "problem_only",
                "model": "mock"}
    return AgentCoordinator("t1", {"content_type": "movie"}, strategy)


def test_coordinator_processes_fitting_segment():
    coord = _coord()
    texts, records = coord.run(
        [UK_SHORT], [(0, 8000)], ["A short English source line."],
        src_lang="en", tgt_lang="uk", raw_mt_segments=[UK_SHORT],
    )
    assert texts[0].strip()
    assert records[0].ai_adaptation_trace.get("agent_timeline")


def test_coordinator_never_emits_empty_or_english():
    coord = _coord()
    # Overflowing segment, LLM disabled → must keep a valid non-empty uk line.
    with mock.patch.object(ta, "llm_rephrase_available", lambda: False):
        texts, records = coord.run(
            [UK_LONG], [(0, 900)], ["A long English source line that overflows."],
            src_lang="en", tgt_lang="uk", raw_mt_segments=[UK_LONG],
        )
    assert texts[0].strip()
    # Not the English source, not empty.
    assert "English" not in texts[0]
    assert texts[0] != ""


def test_coordinator_quality_fallback_never_emits_truncated_text():
    coord = AgentCoordinator(
        "t-quality-fallback",
        {"content_type": "movie"},
        {"use_llm": False, "speed_mode": "balanced", "llm_policy": "off", "model": "mock"},
    )
    bad = "Джордж подався до Каліфор-"
    texts, records = coord.run(
        [bad], [(0, 5000)], [""],
        src_lang="en", tgt_lang="uk", raw_mt_segments=[bad],
    )
    assert texts[0] == "Репліка завершена."
    chain = records[0].ai_adaptation_trace["agent_timeline"]
    qrows = [step for step in chain if step["agent"] == "quality"]
    assert qrows
    assert qrows[-1]["ok"] is False
    assert qrows[-1]["route_back_to"] == "grammar"


def test_coordinator_parallel_multiple_segments():
    coord = _coord()
    segs = [UK_SHORT, UK_LONG, UK_SHORT, UK_LONG]
    timing = [(0, 8000), (0, 1200), (0, 8000), (0, 1200)]
    srcs = ["Short.", "A long line.", "Short.", "A long line."]
    with mock.patch.dict(os.environ, {"VM_ADAPT_MAX_WORKERS": "4"}), \
         mock.patch.object(ta, "llm_rephrase_available", lambda: False):
        texts, records = coord.run(
            segs, timing, srcs, src_lang="en", tgt_lang="uk", raw_mt_segments=segs,
        )
    assert len(texts) == 4
    assert all(t.strip() for t in texts)
    assert len(records) == 4


def test_live_ai_core_path_uses_timing_agent_and_completes_handoff(monkeypatch):
    from engines.ai_core import get_ai_core, release_ai_core
    import engines.ai_core.core as core_mod
    from engines.ai_adaptation_engine import set_adaptation_profile_override

    monkeypatch.setattr(core_mod, "_agents_enabled", lambda: True)
    core = get_ai_core("task-aicore-timing-live")
    try:
        core.analyze(
            source_segments=["A very long line that needs timing adaptation."],
            timing_map=[{"start": 0, "end": 1200}],
            src_lang="en",
            tgt_lang="uk",
            content_mode_hint="movie",
        )
        core.plan(requested_mode="balanced")
        texts, records = core.adapt_segments(
            [UK_LONG],
            [{"start": 0, "end": 1200}],
            ["A very long line that needs timing adaptation."],
            src_lang="en",
            tgt_lang="uk",
            raw_mt_segments=[UK_LONG],
        )
    finally:
        set_adaptation_profile_override(None)
        release_ai_core("task-aicore-timing-live")

    chain = records[0].ai_adaptation_trace["agent_timeline"]
    timing = [step for step in chain if step["agent"] == "timing"][0]
    assert timing["input_data"]["slot_ms"] == 1200
    assert timing["output_data"]["text"]
    assert timing["reason"] != "not_needed"
    assert texts[0].strip()


def test_live_ai_core_path_uses_quality_agent_as_final_gate(monkeypatch):
    from engines.ai_core import get_ai_core, release_ai_core
    import engines.ai_core.core as core_mod
    from engines.ai_adaptation_engine import set_adaptation_profile_override

    monkeypatch.setattr(core_mod, "_agents_enabled", lambda: True)
    core = get_ai_core("task-aicore-quality-live")
    bad = "Джордж подався до Каліфор-"
    try:
        core.analyze(
            source_segments=[""],
            timing_map=[{"start": 0, "end": 5000}],
            src_lang="en",
            tgt_lang="uk",
            content_mode_hint="movie",
        )
        core.plan(requested_mode="balanced")
        texts, records = core.adapt_segments(
            [bad],
            [{"start": 0, "end": 5000}],
            [""],
            src_lang="en",
            tgt_lang="uk",
            raw_mt_segments=[bad],
        )
    finally:
        set_adaptation_profile_override(None)
        release_ai_core("task-aicore-quality-live")

    chain = records[0].ai_adaptation_trace["agent_timeline"]
    quality = [step for step in chain if step["agent"] == "quality"]
    assert quality
    assert quality[-1]["ok"] is False
    assert quality[-1]["route_back_to"] == "grammar"
    assert texts[0] == "Репліка завершена."


# ── Voice Agent ──────────────────────────────────────────────────────────────
def test_voice_agent_never_changes_text():
    from engines.ai_core.agents.agents_meta import VoiceAgent

    ctx = _ctx(text=UK_SHORT, raw_translation=UK_SHORT)
    res = VoiceAgent().run(ctx)
    assert res.changed is False
    assert res.text == UK_SHORT
    assert ctx.text == UK_SHORT
    assert ctx.voice
    assert res.reason == "voice_decided"


def test_live_ai_core_path_uses_voice_agent_without_altering_text(monkeypatch):
    from engines.ai_core import get_ai_core, release_ai_core
    import engines.ai_core.core as core_mod
    from engines.ai_adaptation_engine import set_adaptation_profile_override

    monkeypatch.setattr(core_mod, "_agents_enabled", lambda: True)
    core = get_ai_core("task-aicore-voice-live")
    try:
        core.analyze(
            source_segments=["A short dramatic line."],
            timing_map=[{"start": 0, "end": 8000}],
            src_lang="en",
            tgt_lang="uk",
            content_mode_hint="movie",
        )
        core.plan(requested_mode="balanced")
        texts, records = core.adapt_segments(
            [UK_SHORT],
            [{"start": 0, "end": 8000}],
            ["A short dramatic line."],
            src_lang="en",
            tgt_lang="uk",
            raw_mt_segments=[UK_SHORT],
        )
    finally:
        set_adaptation_profile_override(None)
        release_ai_core("task-aicore-voice-live")

    chain = records[0].ai_adaptation_trace["agent_timeline"]
    voice = [step for step in chain if step["agent"] == "voice"][0]
    assert voice["reason"] == "voice_decided"
    assert voice["changed"] is False
    assert voice["diagnostics"]["voice"]
    assert texts[0] == UK_SHORT


# ── Mix Agent ────────────────────────────────────────────────────────────────
def test_mix_agent_never_changes_text():
    from engines.ai_core.agents.agents_meta import MixAgent

    ctx = _ctx(text=UK_SHORT, raw_translation=UK_SHORT)
    res = MixAgent().run(ctx)
    assert res.changed is False
    assert res.text == UK_SHORT
    assert res.reason == "project_level"
    assert res.diagnostics["mix_plan"]["output"] == "final_mp4"


# ── OpenDDF AI Agent Timeline ────────────────────────────────────────────────
def test_openddf_ai_agent_timeline_shape():
    from engines.ai_core.report import build_ai_agent_timeline

    coord = _coord()
    _texts, records = coord.run(
        [UK_SHORT], [(0, 8000)], ["Short."], src_lang="en", tgt_lang="uk",
        raw_mt_segments=[UK_SHORT],
    )
    task_info = {"timing_aware_records": [r.to_dict() for r in records]}
    tl = build_ai_agent_timeline(task_info)
    assert tl["enabled"] is True
    assert tl["segment_count"] == 1
    assert tl["agent_order"][0] == "planner"
    assert tl["segments"][0]["chain"]
    assert "per_agent" in tl


def test_ai_core_report_includes_agent_timeline():
    from engines.ai_core.report import build_ai_core_report

    coord = _coord()
    _texts, records = coord.run(
        [UK_SHORT], [(0, 8000)], ["Short."], src_lang="en", tgt_lang="uk",
        raw_mt_segments=[UK_SHORT],
    )
    task_info = {
        "ai_core": {"profile": {"content_type": "movie"}, "strategy": {"model": "mock"}},
        "timing_aware_records": [r.to_dict() for r in records],
    }
    report = build_ai_core_report(task_info)
    assert "ai_agent_timeline" in report
    assert report["ai_agent_timeline"]["enabled"] is True


def test_raw_mt_texts_from_info_prefers_audit_raw_translation():
    from api.auto_dub_api import _raw_mt_texts_from_info

    info = {
        "source_segments": ["Hello"],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": "Привіт raw",
                "final_text": "Привіт final",
            }
        ],
        "segments_data": [{"text": "Привіт from segments"}],
    }
    assert _raw_mt_texts_from_info(info) == ["Привіт raw"]
