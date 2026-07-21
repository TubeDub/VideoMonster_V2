"""MF-HOTFIX — MF called before LOCK; applied=True for rewrite/already_fits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.meaning_fit.flags import (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_BEFORE_LOCK,
    VM_FLAG_MEANING_FIT_EXPAND,
    VM_FLAG_MEANING_FIT_SHORTEN,
    ensure_meaning_fit_enabled_for_dubbing,
    meaning_fit_before_lock_flag,
    meaning_fit_flag,
)
from engines.meaning_fit.orchestrator import apply_meaning_fit_before_lock
from engines.meaning_fit.skeleton import reject_truncate_as_success

ROOT = Path(__file__).resolve().parents[1]
GOAT = json.loads((ROOT / "tests" / "fixtures" / "mf0_goat.json").read_text(encoding="utf-8"))


@pytest.fixture
def dubbing_mf_on(monkeypatch):
    for k in (
        VM_FLAG_MEANING_FIT,
        VM_FLAG_MEANING_FIT_SHORTEN,
        VM_FLAG_MEANING_FIT_EXPAND,
        VM_FLAG_MEANING_FIT_BEFORE_LOCK,
    ):
        monkeypatch.delenv(k, raising=False)
    applied = ensure_meaning_fit_enabled_for_dubbing()
    assert applied[VM_FLAG_MEANING_FIT] == "1"
    assert meaning_fit_flag() is True
    assert meaning_fit_before_lock_flag() is True
    yield


def test_hotfix_overflow_uk_mf_before_lock_applied(dubbing_mf_on):
    """mock overflow UK → MF called before lock → applied True."""
    info = {"translation_locked": False}
    segs = [
        {
            "segment_id": "a" * 32,
            "plain_text": GOAT["long_uk"],
            "translated_text": GOAT["long_uk"],
            "slot_ms": GOAT["slot_ms"],
            "original": GOAT["en_slot_text"],
            "status": "SUCCESS",
            "success": True,
        }
    ]
    assert info.get("translation_locked") is False
    rep = apply_meaning_fit_before_lock(
        segs,
        task_info=info,
        call_site="test_hotfix:BEFORE_lock",
    )
    assert "BEFORE" in (rep.get("call_site") or "") or "before" in (
        rep.get("call_site") or ""
    ).lower() or "lock" in (rep.get("call_site") or "").lower()
    assert info.get("meaning_fit_before_lock") is True
    assert info.get("meaning_fit_done") is True
    assert info.get("translation_locked") is False  # still unlocked for caller to LOCK
    assert segs[0].get("meaning_fit_attempted") is True
    assert segs[0].get("meaning_fit_applied") is True
    assert segs[0].get("meaning_fit_status") == "paraphrase_shorten"
    assert segs[0]["plain_text"] == GOAT["short_paraphrase_uk"]
    assert rep.get("applied", 0) >= 1

    # Caller locks AFTER fit
    info["translation_locked"] = True
    assert segs[0]["meaning_fit_applied"] is True


def test_hotfix_early_lock_recovers_and_applies(dubbing_mf_on):
    info = {"translation_locked": True}
    segs = [
        {
            "segment_id": "b" * 32,
            "plain_text": GOAT["long_uk"],
            "translated_text": GOAT["long_uk"],
            "slot_ms": GOAT["slot_ms"],
        }
    ]
    rep = apply_meaning_fit_before_lock(segs, task_info=info)
    assert rep.get("early_lock_recovered") is True
    assert segs[0].get("meaning_fit_applied") is True
    assert info.get("meaning_fit_needs_relock") is True


def test_hotfix_already_fits_applied_true(dubbing_mf_on):
    info = {"translation_locked": False}
    segs = [
        {
            "segment_id": "c" * 32,
            "plain_text": GOAT["short_paraphrase_uk"],
            "translated_text": GOAT["short_paraphrase_uk"],
            "slot_ms": GOAT["slot_ms"],
        }
    ]
    apply_meaning_fit_before_lock(segs, task_info=info)
    assert segs[0]["meaning_fit_applied"] is True
    assert segs[0]["meaning_fit_reason"] == "already_fits"


def test_hotfix_truncate_not_success(dubbing_mf_on):
    from engines.meaning_fit.exceptions import TruncateNotMeaningFitError

    with pytest.raises(TruncateNotMeaningFitError):
        reject_truncate_as_success("truncate_to_n_chars", text_uk=GOAT["bad_truncate_uk"])


def test_hotfix_psa_identity_smoke(dubbing_mf_on, monkeypatch):
    monkeypatch.setenv("VM_FLAG_IDENTITY_GUARD", "1")
    from engines.pipeline_integrity.identity_guard import assert_consistent

    text = GOAT["short_paraphrase_uk"]
    rep = assert_consistent(
        [
            {
                "segment_id": "d" * 32,
                "plain_text": text,
                "translated_text": text,
                "final_tts_text": text,
            }
        ],
        stage="mf_hotfix",
    )
    assert rep.get("ok") is True


def test_hotfix_env_off_still_respected(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "0")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_BEFORE_LOCK, "0")
    ensure_meaning_fit_enabled_for_dubbing()  # must not override explicit 0
    assert meaning_fit_flag() is False
