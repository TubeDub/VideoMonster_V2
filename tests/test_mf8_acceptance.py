"""MF8 — final Meaning Fit acceptance (no new features)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.meaning_fit.duration_predictor import predict_vs_slot
from engines.meaning_fit.flags import (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_BEFORE_LOCK,
    VM_FLAG_MEANING_FIT_EXPAND,
    VM_FLAG_MEANING_FIT_SHORTEN,
)
from engines.meaning_fit.orchestrator import apply_meaning_fit_before_lock, fit_segment
from engines.meaning_fit.skeleton import reject_truncate_as_success
from engines.meaning_fit.types import FitRequest

ROOT = Path(__file__).resolve().parents[1]
GOAT = json.loads((ROOT / "tests" / "fixtures" / "mf0_goat.json").read_text(encoding="utf-8"))


@pytest.fixture
def mf_on(monkeypatch):
    for k in (
        VM_FLAG_MEANING_FIT,
        VM_FLAG_MEANING_FIT_SHORTEN,
        VM_FLAG_MEANING_FIT_EXPAND,
        VM_FLAG_MEANING_FIT_BEFORE_LOCK,
    ):
        monkeypatch.setenv(k, "1")
    yield


def test_mf8_goat_ok_truncate_bad(mf_on):
    res = fit_segment(
        FitRequest(text_uk=GOAT["long_uk"], slot_ms=GOAT["slot_ms"]),
        force=True,
    )
    assert res.success is True
    assert res.text_uk == GOAT["short_paraphrase_uk"]
    from engines.meaning_fit.exceptions import TruncateNotMeaningFitError
    import pytest as _pt

    with _pt.raises(TruncateNotMeaningFitError):
        reject_truncate_as_success("truncate_to_n_chars", text_uk=GOAT["bad_truncate_uk"])


def test_mf8_too_long_too_short(mf_on):
    assert predict_vs_slot(GOAT["long_uk"], GOAT["slot_ms"]).verdict == "TOO_LONG"
    assert predict_vs_slot(GOAT["short_paraphrase_uk"], GOAT["slot_ms"]).verdict == "OK"
    assert predict_vs_slot("Коза", 2500).verdict == "TOO_SHORT"


def test_mf8_lock_after_fit(mf_on):
    info = {"translation_locked": False}
    segs = [
        {
            "segment_id": "a" * 32,
            "plain_text": GOAT["long_uk"],
            "translated_text": GOAT["long_uk"],
            "slot_ms": GOAT["slot_ms"],
        }
    ]
    apply_meaning_fit_before_lock(segs, task_info=info)
    assert info["meaning_fit_before_lock"] is True
    # LOCK only after MF
    info["translation_locked"] = True
    assert segs[0]["plain_text"] == GOAT["short_paraphrase_uk"]


def test_mf8_flags_off_legacy(monkeypatch):
    for k in (
        VM_FLAG_MEANING_FIT,
        VM_FLAG_MEANING_FIT_SHORTEN,
        VM_FLAG_MEANING_FIT_EXPAND,
        VM_FLAG_MEANING_FIT_BEFORE_LOCK,
    ):
        monkeypatch.setenv(k, "0")
    res = fit_segment(FitRequest(text_uk=GOAT["long_uk"], slot_ms=GOAT["slot_ms"]))
    assert res.status == "noop"
    assert res.text_uk == GOAT["long_uk"]


def test_mf8_psa_smoke_identity_guard(mf_on, monkeypatch):
    monkeypatch.setenv("VM_FLAG_IDENTITY_GUARD", "1")
    from engines.pipeline_integrity.identity_guard import assert_consistent

    rows = [
        {
            "segment_id": "b" * 32,
            "plain_text": GOAT["short_paraphrase_uk"],
            "translated_text": GOAT["short_paraphrase_uk"],
            "final_tts_text": GOAT["short_paraphrase_uk"],
        }
    ]
    rep = assert_consistent(rows, stage="mf8")
    assert rep.get("ok") is True
