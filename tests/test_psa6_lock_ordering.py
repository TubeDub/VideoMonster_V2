"""PSA6 — LOCK ordering + residual overflow honesty (ba6ec-like)."""

from __future__ import annotations

import pytest

from engines.pipeline_integrity.overflow_inspector import (
    CRITICAL_OVERFLOW_MS,
    SYNC_FAIL,
    apply_late_translation_lock,
    apply_psa6_lock_ordering,
    assert_no_success_with_residual_overflow,
    inspect_overflow,
    unlock_for_overflow_remediation,
)


@pytest.fixture
def overflow_on(monkeypatch):
    monkeypatch.setenv("VM_OVERFLOW_INSPECTOR", "1")
    yield


def _overflow_seg(**extra):
    row = {
        "segment_id": "d" * 32,
        "slot_ms": 1000,
        "playback_duration": 1000 + CRITICAL_OVERFLOW_MS + 400,  # huge residual
        "status": "SUCCESS",
        "success": True,
        "plain_text": "Long dubbed line that still overflows the slot badly.",
    }
    row.update(extra)
    return row


def test_psa6_overflow_locked_deadend_unlocks(overflow_on):
    """ba6ec-like: translation_locked=true + huge overflow → unlock + call-points."""
    segs = [
        _overflow_seg(translation_locked=True, index=0),
        {
            "segment_id": "e" * 32,
            "slot_ms": 5000,
            "playback_duration": 4800,
            "status": "SUCCESS",
            "success": True,
            "translation_locked": True,
        },
    ]
    info = {
        "translation_locked": True,
        "segments_data": segs,
        "status": "SUCCESS",
    }

    result = apply_psa6_lock_ordering(info, segs, slot_budget_ok=True)

    assert result["translation_locked"] is False
    assert info.get("translation_locked") is False
    assert info.get("translation_lock_invalidated") is True
    assert result["meaning_fit_call_point"] is True
    assert result["manual_review_call_point"] is True
    assert result["pipeline_success_allowed"] is False

    # Overflowing row honesty
    bad = segs[0]
    assert bad.get("translation_locked") is False
    assert bad.get("needs_manual_review") is True
    assert bad.get("sync_status") == SYNC_FAIL
    assert bad.get("status") == SYNC_FAIL
    assert bad.get("success") is False
    assert bad.get("meaning_fit_call_point") is True
    assert bad.get("manual_review_call_point") is True

    # Late lock must not re-lock
    assert result["late_lock"].get("locked") is False
    assert result["late_lock"].get("unlocked_for_remediation") is True


def test_psa6_residual_overflow_forbids_success(overflow_on):
    segs = [_overflow_seg()]
    report = inspect_overflow(segs)
    assert report["success_allowed"] is False
    assert segs[0]["status"] == SYNC_FAIL
    assert segs[0]["needs_manual_review"] is True

    # Even if someone stamps SUCCESS again — honesty demotes
    segs[0]["status"] = "SUCCESS"
    segs[0]["success"] = True
    honesty = assert_no_success_with_residual_overflow(segs)
    assert honesty["ok"] is False
    assert honesty["demoted"] >= 1
    assert segs[0]["status"] == SYNC_FAIL
    assert segs[0]["success"] is False


def test_psa6_fit_ok_may_lock(overflow_on, monkeypatch):
    """No residual overflow → late lock path may proceed (lock fn stubbed)."""
    segs = [
        {
            "segment_id": "a" * 32,
            "slot_ms": 4000,
            "playback_duration": 3900,
            "status": "SUCCESS",
            "success": True,
        }
    ]
    info = {"segments_data": segs, "translation_locked": False}

    def _fake_lock(task_info):
        task_info["translation_locked"] = True
        for s in task_info.get("segments_data") or []:
            s["translation_locked"] = True
        return task_info

    monkeypatch.setattr(
        "engines.translation_validation.apply_translation_lock_after_validation",
        _fake_lock,
    )
    res = apply_late_translation_lock(info, segs, slot_budget_ok=True)
    assert res["locked"] is True
    assert info.get("meaning_fit_call_point") is not True


def test_psa6_unlock_helper_sets_call_points(overflow_on):
    segs = [_overflow_seg(translation_locked=True)]
    info = {"translation_locked": True, "segments_data": segs}
    ordering = unlock_for_overflow_remediation(
        info, segs, reason="critical_overflow"
    )
    assert ordering["translation_locked"] is False
    assert "meaning_fit" in ordering["next_call_points"]
    assert info["manual_review_call_point"] is True
