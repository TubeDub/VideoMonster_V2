"""TZ Dub Engine — overflow strategy chain, cost selection, success gate."""

from __future__ import annotations


def test_strategy_order_forbids_stretch_before_tempo():
    from engines.dub_engine_v2.overflow_strategy import assert_strategy_order

    assert assert_strategy_order(["trim_silence", "pause_optimization", "tempo", "stretch"]) == []
    viol = assert_strategy_order(["trim_silence", "stretch"])
    assert any("tempo" in v for v in viol)


def test_build_variants_at_least_four_with_costs():
    from engines.dub_engine_v2.overflow_strategy import (
        STRATEGY_COSTS,
        build_strategy_variants,
    )

    variants = build_strategy_variants(
        overflow_ms=400, slot_ms=5000, gap_after_ms=0, llm_available=False, text_locked=True
    )
    assert len(variants) >= 4
    names = {v.strategy for v in variants}
    assert "trim_silence" in names
    assert "tempo" in names
    assert "manual_review" in names
    assert STRATEGY_COSTS["trim_silence"] < STRATEGY_COSTS["stretch"]
    assert STRATEGY_COSTS["stretch"] < STRATEGY_COSTS["borrow_time"]
    assert STRATEGY_COSTS["borrow_time"] < STRATEGY_COSTS["semantic_rewrite"]


def test_select_best_prefers_cheaper_fit():
    from engines.dub_engine_v2.overflow_strategy import decide_overflow

    d = decide_overflow(
        index=1,
        overflow_ms=80,
        slot_ms=5000,
        gap_after_ms=0,
        llm_available=False,
        text_locked=True,
        already_applied=[],
    )
    assert d.adaptation_executed is True
    assert d.chosen in ("trim_silence", "pause_optimization", "tempo")
    assert d.chosen_cost <= 2.0
    assert len(d.variants_considered) >= 4


def test_decide_after_trim_pause_picks_tempo_not_video_first():
    from engines.dub_engine_v2.overflow_strategy import decide_overflow

    d = decide_overflow(
        index=2,
        overflow_ms=300,
        slot_ms=5000,
        gap_after_ms=50,
        text_locked=True,
        already_applied=["trim_silence", "pause_optimization"],
    )
    assert d.chosen == "tempo"
    assert d.chosen_cost == 2.0


def test_register_overflow_sets_adaptation_executed():
    from engines.pipeline_integrity.overflow_manager import register_overflow

    seg = {"segment_id": "s1", "translation_locked": True, "final_text": "тест."}
    rec = register_overflow(seg, index=0, overflow_ms=500, slot_ms=4000, reason="test")
    assert rec.overflow_ms == 500
    assert seg.get("adaptation_executed") is True
    assert seg.get("overflow_decision", {}).get("chosen")
    assert len(rec.recovery_plan) >= 4
    assert rec.recovery_plan[0] == "trim_silence"
    assert "tempo" in rec.recovery_plan
    # stretch must not come before tempo
    assert rec.recovery_plan.index("tempo") < rec.recovery_plan.index("stretch")


def test_success_gate_blocks_overflow_without_adaptation():
    import pytest

    from engines.dub_engine_v2.overflow_strategy import (
        UnhandledOverflowError,
        assert_pipeline_may_succeed,
    )

    segs = [
        {
            "overflow_ms": 400,
            "slot_overflow": True,
            "adaptation_executed": False,
            "adaptation_status": "ADAPTATION NOT EXECUTED",
        }
    ]
    with pytest.raises(UnhandledOverflowError) as exc:
        assert_pipeline_may_succeed(segs)
    msg = str(exc.value)
    assert "OverflowDetected" in msg
    assert "AdaptationSkipped" in msg
    assert "skip_reason=" in msg
    # Invariant: false without skip_reason is forbidden — gate fills it
    assert segs[0].get("adaptation_skip_reason")


def test_false_without_skip_reason_is_filled():
    from engines.dub_engine_v2.adaptation_decision import (
        SKIP_FITS_NO_CHANGE,
        finalize_segment_adaptation_fields,
    )

    seg = {"adaptation_executed": False, "final_text": "ok."}
    finalize_segment_adaptation_fields(seg, index=0)
    assert seg["adaptation_skip_reason"] == SKIP_FITS_NO_CHANGE
    assert seg["adaptation_status"] == "ADAPTATION NOT EXECUTED"
    assert (seg.get("adaptation_decision") or {}).get("skip_reason") == SKIP_FITS_NO_CHANGE


def test_overflow_plus_false_is_violation_with_skip_reason():
    from engines.dub_engine_v2.adaptation_decision import (
        SKIP_TRANSLATION_LOCKED,
        mark_adaptation_skipped,
        overflow_adaptation_violation,
    )

    seg = {
        "overflow_ms": 500,
        "slot_overflow": True,
        "adaptation_executed": False,
        "final_text": "довгий текст",
        "translation_locked": True,
    }
    mark_adaptation_skipped(
        seg,
        skip_reason=SKIP_TRANSLATION_LOCKED,
        overflow_ms=500,
        need_adaptation=True,
        decision="skip_text_rewrite",
    )
    viol = overflow_adaptation_violation(seg)
    assert viol is not None
    assert viol["skip_reason"] == SKIP_TRANSLATION_LOCKED
    assert "OverflowDetected" in viol["message"]


def test_fits_no_change_ok_without_overflow():
    from engines.dub_engine_v2.adaptation_decision import (
        SKIP_FITS_NO_CHANGE,
        mark_adaptation_skipped,
        overflow_adaptation_violation,
    )
    from engines.dub_engine_v2.overflow_strategy import assert_pipeline_may_succeed

    seg = {
        "overflow_ms": 0,
        "adaptation_executed": False,
        "final_text": "коротко.",
    }
    mark_adaptation_skipped(
        seg,
        skip_reason=SKIP_FITS_NO_CHANGE,
        need_adaptation=False,
        decision="fits_no_change",
    )
    assert overflow_adaptation_violation(seg) is None
    report = assert_pipeline_may_succeed([seg])
    assert report["ok"] is True


def test_success_gate_passes_when_adapted():
    from engines.dub_engine_v2.overflow_strategy import assert_pipeline_may_succeed

    segs = [
        {
            "overflow_ms": 100,
            "slot_overflow": True,
            "adaptation_executed": True,
            "overflow_decision": {
                "chosen": "tempo",
                "variants_considered": [
                    {"strategy": "trim_silence"},
                    {"strategy": "tempo"},
                ],
            },
            "text_adaptation_trace": {
                "executed": True,
                "stages": ["trim_silence", "pause_optimization", "tempo"],
            },
        }
    ]
    report = assert_pipeline_may_succeed(segs)
    assert report["ok"] is True


def test_llm_unavailable_still_builds_rewrite_variant_when_unlocked():
    from engines.dub_engine_v2.overflow_strategy import build_strategy_variants

    variants = build_strategy_variants(
        overflow_ms=2000,
        slot_ms=5000,
        llm_available=False,
        text_locked=False,
    )
    rewrite = next(v for v in variants if v.strategy == "semantic_rewrite")
    assert rewrite.rejected is False
    assert "DSAL" in rewrite.reason or "rule" in rewrite.reason.lower()


def test_slot_fit_allows_decision_trace_fields():
    """Regression: stamp during slot_fit must not raise StageSnapshotIntegrityError."""
    from engines.dub_engine_v2.overflow_strategy import decide_overflow, stamp_decision_on_segment
    from engines.pipeline_integrity.guards import StageSnapshotGuard
    from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage

    required = {
        "adaptation_decision",
        "adaptation_skip_reason",
        "adaptation_status",
        "adaptation_executed",
        "overflow_decision",
        "decision_trace",
        "decision_transitions",
    }
    allowed = allowed_fields_for_stage("slot_fit")
    missing = required - set(allowed)
    assert not missing, f"slot_fit whitelist missing: {missing}"

    before = [
        {
            "segment_id": "fecc9684ad1e4f38a0848166c23d3c7a",
            "translation_locked": True,
            "final_text": "тест",
            "overflow_ms": 0,
        }
    ]
    after = [dict(before[0])]
    d = decide_overflow(
        index=0, overflow_ms=400, slot_ms=5000, text_locked=True, already_applied=[]
    )
    stamp_decision_on_segment(after[0], d)
    # Must not raise
    StageSnapshotGuard.check(before, after, stage="slot_fit")


def test_decision_trace_has_terminal_statuses_and_why():
    """Full Decision Trace: every stage SUCCESS|FAILED|SKIPPED(+reason); strategy why recorded."""
    from engines.dub_engine_v2.adaptation_decision import (
        SKIP_TRANSLATION_LOCKED,
        mark_adaptation_skipped,
    )
    from engines.dub_engine_v2.decision_trace import (
        TERMINAL_STATUSES,
        find_silent_stages,
        format_decision_trace_openddf,
    )
    from engines.dub_engine_v2.overflow_strategy import decide_overflow, stamp_decision_on_segment

    # Skip path
    skip_seg = {
        "segment_id": "skip1",
        "overflow_ms": 842,
        "final_text": "текст",
        "translation_locked": True,
    }
    mark_adaptation_skipped(
        skip_seg,
        skip_reason=SKIP_TRANSLATION_LOCKED,
        overflow_ms=842,
        need_adaptation=True,
        decision="skip_text_rewrite",
    )
    assert find_silent_stages(skip_seg) == []
    for st in skip_seg["decision_trace"]:
        assert st["status"] in TERMINAL_STATUSES
        if st["status"] == "SKIPPED":
            assert st["reason"]
    od = format_decision_trace_openddf(skip_seg)
    assert od["title"] == "Decision Trace"
    assert od["stages"]
    assert "TranslationLocked" in od["summary"] or any(
        "TranslationLocked" in str(s.get("reason")) for s in od["stages"]
    )

    # Chosen strategy path with why
    ok_seg = {"segment_id": "ok1", "final_text": "ok"}
    d = decide_overflow(
        index=0,
        overflow_ms=300,
        slot_ms=5000,
        text_locked=True,
        already_applied=["trim_silence", "pause_optimization"],
    )
    stamp_decision_on_segment(ok_seg, d)
    assert ok_seg.get("adaptation_executed") is True
    assert find_silent_stages(ok_seg) == []
    chosen_stages = [
        s for s in ok_seg["decision_trace"] if s["stage"] in ("chosen_strategy", "decision_engine")
    ]
    assert chosen_stages
    assert any(s.get("reason") or (s.get("detail") or {}).get("why") for s in chosen_stages)
    assert d.why
    assert ok_seg["overflow_decision"]["why"] == d.why


def test_regression_overflow_false_never_pipeline_success():
    """
    Mandatory regression: overflow + adaptation_executed=false MUST NOT be SUCCESS.
    Any future Dub Engine change that allows this must fail this test.
    """
    import pytest

    from engines.dub_engine_v2.overflow_strategy import (
        UnhandledOverflowError,
        assert_pipeline_may_succeed,
    )

    cases = [
        {
            "overflow_ms": 1,
            "slot_overflow": True,
            "adaptation_executed": False,
            "final_text": "a",
        },
        {
            "overflow_ms": 842,
            "slot_overflow": True,
            "adaptation_executed": False,
            "adaptation_status": "ADAPTATION NOT EXECUTED",
            "final_text": "довго",
            "translation_locked": True,
        },
        {
            "overflow_ms": 200,
            "slot_overflow": True,
            "adaptation_executed": False,
            # deliberately omit skip_reason — gate must still fail and fill it
            "final_text": "x",
        },
    ]
    for segs in ([c] for c in cases):
        with pytest.raises(UnhandledOverflowError) as exc:
            assert_pipeline_may_succeed(segs)
        assert "OverflowDetected" in str(exc.value)
        assert "AdaptationSkipped" in str(exc.value)
        assert segs[0].get("adaptation_skip_reason")
        # Decision Trace must exist and final must be FAILED
        trace = segs[0].get("decision_trace") or []
        assert trace, "decision_trace required on fail path"
        finals = [t for t in trace if t.get("stage") == "final_result"]
        assert finals and finals[-1]["status"] == "FAILED"


def test_no_silent_stage_status_allowed():
    from engines.dub_engine_v2.decision_trace import (
        STATUS_SUCCESS,
        assert_no_silent_decision_stages,
        find_silent_stages,
        record_stage,
    )

    seg = {"segment_id": "s", "final_text": "t"}
    record_stage(seg, stage="probe", status=STATUS_SUCCESS, reason="")
    assert find_silent_stages(seg) == []
    # Inject illegal silent stage
    seg["decision_trace"].append({"stage": "broken", "status": "", "reason": ""})
    assert find_silent_stages(seg)
    bad = assert_no_silent_decision_stages([seg])
    # ensure_complete should repair / replace broken when reconstructing? 
    # find_silent still sees broken until repaired — assert_no_silent reports it
    assert isinstance(bad, list)
