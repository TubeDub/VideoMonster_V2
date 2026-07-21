"""Pipeline Integrity & Timing Architecture v2.0 — unit tests."""

from __future__ import annotations


def test_micro_slot_merge_850ms():
    from engines.pipeline_integrity.segment_normalizer import merge_micro_slots

    segs = ["Hi.", "He drove home for dinner after a long day."]
    timing = [
        {"start": 0, "end": 600},  # micro
        {"start": 600, "end": 5600},
    ]
    texts, tm, report = merge_micro_slots(segs, timing, min_ms=850)
    assert report["merged"] >= 1
    assert len(texts) == 1
    assert "Hi." in texts[0]
    assert tm[0]["end"] - tm[0]["start"] >= 850


def test_identity_guard_rejects_index_id():
    from engines.pipeline_integrity.exceptions import IdentityMismatchError
    from engines.pipeline_integrity.identity_guard import verify_identity_chain

    segs = [{"segment_id": "0", "plain_text": "x", "owned_text_segment_id": "0"}]
    try:
        verify_identity_chain(segs, stage="test", force=True)
        assert False, "expected IdentityMismatchError"
    except IdentityMismatchError as exc:
        assert "UUID" in str(exc) or "index" in str(exc).lower()


def test_identity_guard_ok_uuid():
    from engines.pipeline_integrity.identity_guard import verify_identity_chain

    sid = "a1b2c3d4e5f6789012345678abcdef01"
    segs = [
        {
            "segment_id": sid,
            "plain_text": "Hello world",
            "owned_text_segment_id": sid,
            "translation_uuid": "t" * 32,
        }
    ]
    report = verify_identity_chain(segs, stage="test", force=True)
    assert report["ok"] is True


def test_slot_budget_blocks_impossible_fit(monkeypatch):
    # PSA1: Slot Budget default OFF — unit test enables the gate explicitly.
    monkeypatch.setenv("VM_FLAG_SLOT_BUDGET", "1")
    from engines.pipeline_integrity.slot_budget import compute_slot_budgets

    segs = [
        {
            "segment_id": "a" * 32,
            "plain_text": "Это очень длинный украинский текст " * 20,
            "slot_ms": 900,
        }
    ]
    report = compute_slot_budgets(segs, tgt_lang="uk")
    assert report.tts_allowed is False
    assert report.blocked
    assert segs[0].get("slot_budget")


def test_overflow_inspector_blocks_success():
    from engines.pipeline_integrity.overflow_inspector import (
        inspect_overflow,
        should_lock_translations,
    )

    segs = [
        {
            "segment_id": "b" * 32,
            "slot_ms": 1000,
            "playback_duration": 1600,
            "status": "SUCCESS",
        }
    ]
    report = inspect_overflow(segs)
    assert report["critical"] >= 1
    assert segs[0].get("sync_status") in (
        "SYNC_FAIL",
        "SYNC_OVERFLOW_REMAINING",
    )
    assert segs[0].get("success") is False
    assert segs[0].get("needs_manual_review") is True
    ok, reason = should_lock_translations(segs, slot_budget_ok=True)
    assert ok is False
    assert "overflow" in reason


def test_revision_manager_stamps_adaptation_uuid():
    from engines.pipeline_integrity.revision_manager import note_text_change

    seg = {"segment_id": "c" * 32, "plain_text": "old"}
    note_text_change(seg, "new adapted text", kind="adaptation")
    assert seg.get("adaptation_uuid")
    assert seg["plain_text"] == "new adapted text"
    assert seg.get("revision_chain") or seg.get("text_revision_uuid")


def test_honest_diagnostics_fields():
    from engines.pipeline_integrity.honest_diagnostics import (
        collect_honest_summary,
        set_reason,
    )

    seg = {"segment_id": "d" * 32}
    set_reason(seg, "text_adaptation_reason", "semantic_shortening")
    set_reason(seg, "slot_strategy_reason", "predicted_overflow_critical")
    summary = collect_honest_summary(seg)
    assert summary["text_adaptation_reason"] == "semantic_shortening"
    assert summary["slot_strategy_reason"] == "predicted_overflow_critical"
    assert "text_adaptation_reason:semantic_shortening" in summary["decision_trace"]


def test_v2_gates_list():
    from engines.pipeline_integrity.v2_gates import list_v2_gates

    gates = list_v2_gates()
    for key in (
        "identity_guard",
        "segment_normalizer",
        "slot_budget",
        "revision_manager",
        "smart_segmentation",
        "overflow_inspector",
    ):
        assert key in gates


def test_jr_false_boundary_must_join():
    from engines.smart_segmentation import ends_true_sentence, would_break_forbidden

    prev = "An 18-year-old boy named George Jr."
    nxt = "could not help but feel like he was really dreading actually getting there."
    assert ends_true_sentence(prev) is False
    must, reason = would_break_forbidden(prev, nxt)
    assert must is True
    assert reason in ("lowercase_continuation", "abbrev_false_boundary", "mid_sentence")


def test_normalizer_joins_jr_cut():
    from engines.pipeline_integrity.segment_normalizer import merge_micro_slots

    segs = [
        "An 18-year-old boy named George Jr.",
        "could not help but feel like he was really dreading actually getting there.",
        "So George Jr. was a very smart kid.",
    ]
    timing = [
        {"start": 0, "end": 4200},
        {"start": 4200, "end": 9000},
        {"start": 9000, "end": 16000},
    ]
    texts, _tm, report = merge_micro_slots(segs, timing, min_ms=850)
    assert any("could not help" in t and "George Jr." in t for t in texts)
    assert report.get("continuation_merged", 0) >= 1
    assert len(texts) < 3
