"""MF6 — Meaning Fit before LOCK; IdentityGuard alive; flag off legacy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.meaning_fit.flags import (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_BEFORE_LOCK,
    VM_FLAG_MEANING_FIT_EXPAND,
    VM_FLAG_MEANING_FIT_SHORTEN,
)
from engines.meaning_fit.orchestrator import (
    MEANING_FIT_CALL_SITE,
    apply_meaning_fit_before_lock,
    fit_segment,
)
from engines.meaning_fit.types import FitRequest

ROOT = Path(__file__).resolve().parents[1]
GOAT = json.loads((ROOT / "tests" / "fixtures" / "mf0_goat.json").read_text(encoding="utf-8"))


@pytest.fixture
def mf_all_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_SHORTEN, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_EXPAND, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_BEFORE_LOCK, "1")
    yield


def test_mf6_call_site_documented():
    assert "auto_dub_api.py" in MEANING_FIT_CALL_SITE
    assert "before" in MEANING_FIT_CALL_SITE.lower() or "LOCK" in MEANING_FIT_CALL_SITE


def test_mf6_lock_not_before_mf(mf_all_on):
    """Hotfix: premature LOCK is recovered — MF still runs, then needs re-LOCK."""
    info = {"translation_locked": True, "segments_data": []}
    segs = [
        {
            "segment_id": "a" * 32,
            "plain_text": GOAT["long_uk"],
            "translated_text": GOAT["long_uk"],
            "slot_ms": GOAT["slot_ms"],
            "original": GOAT["en_slot_text"],
        }
    ]
    rep = apply_meaning_fit_before_lock(segs, task_info=info)
    assert rep.get("early_lock_recovered") is True
    assert info.get("meaning_fit_needs_relock") is True
    assert segs[0].get("meaning_fit_applied") is True
    assert segs[0]["plain_text"] == GOAT["short_paraphrase_uk"]


def test_mf6_runs_before_lock_and_shortens(mf_all_on):
    info = {"translation_locked": False}
    segs = [
        {
            "segment_id": "b" * 32,
            "plain_text": GOAT["long_uk"],
            "translated_text": GOAT["long_uk"],
            "slot_ms": GOAT["slot_ms"],
            "original": GOAT["en_slot_text"],
            "status": "SUCCESS",
            "success": True,
        }
    ]
    rep = apply_meaning_fit_before_lock(segs, task_info=info)
    assert rep["enabled"] is True
    assert info.get("meaning_fit_before_lock") is True
    assert info.get("meaning_fit_phase") == "before_lock"
    assert segs[0]["plain_text"] == GOAT["short_paraphrase_uk"]
    assert segs[0]["meaning_fit_status"] == "paraphrase_shorten"


def test_mf6_fit_fail_not_success_on_huge_overflow(mf_all_on):
    info = {"translation_locked": False}
    # Unparaphraseable long line
    long = "слово " * 80
    segs = [
        {
            "segment_id": "c" * 32,
            "plain_text": long,
            "translated_text": long,
            "slot_ms": 800,
            "status": "SUCCESS",
            "success": True,
        }
    ]
    apply_meaning_fit_before_lock(segs, task_info=info)
    assert segs[0].get("needs_manual_review") is True
    assert segs[0].get("success") is False
    assert str(segs[0].get("status")).upper() != "SUCCESS"


def test_mf6_identity_guard_still_works(mf_all_on, monkeypatch):
    monkeypatch.setenv("VM_FLAG_IDENTITY_GUARD", "1")
    from engines.pipeline_integrity.exceptions import IdentityMismatchError
    from engines.pipeline_integrity.identity_guard import assert_consistent

    rows = [
        {
            "segment_id": "d" * 32,
            "plain_text": "один",
            "translated_text": "один",
            "final_tts_text": "два чужий",
        },
        {
            "segment_id": "e" * 32,
            "plain_text": "два чужий",
            "translated_text": "два чужий",
            "final_tts_text": "два чужий",
        },
    ]
    with pytest.raises(IdentityMismatchError):
        assert_consistent(rows, stage="mf6_ig")


def test_mf6_flag_off_legacy(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "0")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_BEFORE_LOCK, "0")
    info = {"translation_locked": False}
    segs = [
        {
            "segment_id": "f" * 32,
            "plain_text": GOAT["long_uk"],
            "translated_text": GOAT["long_uk"],
            "slot_ms": GOAT["slot_ms"],
        }
    ]
    rep = apply_meaning_fit_before_lock(segs, task_info=info)
    assert rep.get("noop") is True
    assert segs[0]["plain_text"] == GOAT["long_uk"]
    res = fit_segment(FitRequest(text_uk=GOAT["long_uk"], slot_ms=GOAT["slot_ms"]))
    assert res.status == "noop"
