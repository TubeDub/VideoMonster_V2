"""MF7 — honest meaning_fit reasons + counters."""

from __future__ import annotations

import pytest

from engines.meaning_fit.diagnostics import (
    apply_honest_meaning_fit_reasons,
    get_counters,
    reset_counters,
)
from engines.meaning_fit.flags import (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_SHORTEN,
)
from engines.meaning_fit.orchestrator import fit_segment
from engines.meaning_fit.types import FitRequest, FitResult


@pytest.fixture(autouse=True)
def _reset():
    reset_counters()
    yield
    reset_counters()


def test_mf7_no_false_semantic_paraphrase_when_text_unchanged():
    seg = {
        "translated_text": "Коза паслась там на лугу",
        "plain_text": "Коза паслась там на лугу",
        "text_adaptation_reason": "semantic_paraphrase",
        "audio_strategy_reason": "trim",
        "meaning_fit_status": "success",
    }
    out = apply_honest_meaning_fit_reasons(seg)
    assert "semantic_paraphrase" not in out["text_adaptation_reason"].lower()
    assert out["text_changed"] is False


def test_mf7_truncate_not_success():
    seg = {
        "translated_text": "Коза паслась на тій горі і їла траву",
        "plain_text": "Коза паслась на тій",
        "meaning_fit_method": "truncate_to_n_chars",
        "status": "SUCCESS",
        "success": True,
    }
    apply_honest_meaning_fit_reasons(
        seg,
        FitResult(
            text_uk=seg["plain_text"],
            status="rejected_truncate",
            reason="rejected_truncate",
            method="truncate_to_n_chars",
            success=False,
        ),
    )
    assert seg["meaning_fit_status"] == "rejected_truncate"
    assert seg.get("success") is False


def test_mf7_fields_and_counters(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_SHORTEN, "1")
    res = fit_segment(
        FitRequest(
            text_uk="Коза паслась на тій горі і їла траву",
            slot_ms=2500,
        ),
        force=True,
    )
    seg = {
        "translated_text": "Коза паслась на тій горі і їла траву",
        "plain_text": "Коза паслась на тій горі і їла траву",
    }
    out = apply_honest_meaning_fit_reasons(seg, res)
    assert "text_adaptation_reason" in out
    assert "audio_strategy_reason" in out
    assert "meaning_fit_status" in out
    assert "residual_overflow_ms" in out
    c = get_counters()
    assert c["shorten"] + c["expand"] + c["fail"] + c["already_fits"] + c["noop"] >= 1
