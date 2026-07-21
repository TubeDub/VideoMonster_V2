"""MF0/MF8 — goat Meaning Fit acceptance (GREEN via MF path).

Historical bad_outcomes remain in fixture as anti-patterns; asserts check
Meaning Fit rejects them and accepts paraphrase.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.meaning_fit.diagnostics import apply_honest_meaning_fit_reasons
from engines.meaning_fit.duration_predictor import predict_vs_slot
from engines.meaning_fit.exceptions import TruncateNotMeaningFitError
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
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "mf0_goat.json"


@pytest.fixture(scope="module")
def goat() -> dict:
    assert FIXTURE_PATH.is_file(), f"missing fixture: {FIXTURE_PATH}"
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert data.get("name") == "goat"
    return data


@pytest.fixture
def mf_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_SHORTEN, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_EXPAND, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_BEFORE_LOCK, "1")
    yield


def test_mf0_fixture_goat_fields(goat):
    assert goat["slot_ms"] > 0
    assert goat["long_uk"].strip()
    assert goat["short_paraphrase_uk"].strip()
    assert goat["bad_truncate_uk"].strip()
    assert goat["short_paraphrase_uk"] != goat["bad_truncate_uk"]
    assert "коза" in goat["short_paraphrase_uk"].lower()


def test_mf0_overflow_without_paraphrase_fails(goat, mf_on):
    """Overflow without paraphrase must fail — MF shortens or FIT_FAIL."""
    bad = goat["bad_outcomes"]["overflow_without_paraphrase"]
    assert int(bad["residual_overflow_ms"]) > 0
    assert bad.get("paraphrase_applied") is False
    # Documented anti-pattern in dump
    assert bad.get("success") is True

    res = fit_segment(
        FitRequest(text_uk=bad["text_uk"], slot_ms=bad["slot_ms"]),
        force=True,
    )
    assert res.status == "paraphrase_shorten" or (
        res.status == "fit_failed" and res.needs_manual
    )
    if res.status == "paraphrase_shorten":
        assert res.success is True
        assert res.text_uk == goat["short_paraphrase_uk"]
    pred = predict_vs_slot(res.text_uk, goat["slot_ms"])
    if res.success:
        assert pred.verdict == "OK"


def test_mf0_truncate_is_not_meaning_fit_success(goat, mf_on):
    bad = goat["bad_outcomes"]["truncate_as_meaning_fit_success"]
    assert bad["method"] == "truncate_to_n_chars"
    assert bad["text_uk"] == goat["bad_truncate_uk"]
    # Dump wrongly labels success — engine must reject
    assert str(bad.get("meaning_fit_status")).lower() == "success"
    with pytest.raises(TruncateNotMeaningFitError):
        reject_truncate_as_success(bad["method"], text_uk=bad["text_uk"])
    res = fit_segment(
        FitRequest(text_uk=goat["long_uk"], slot_ms=goat["slot_ms"]),
        force=True,
    )
    assert res.text_uk != goat["bad_truncate_uk"]
    assert res.reason != "truncate_to_n_chars"
    assert res.success is True


def test_mf0_audio_trim_is_not_semantic_paraphrase(goat, mf_on):
    bad = goat["bad_outcomes"]["audio_trim_as_semantic_paraphrase"]
    assert bad.get("audio_strategy_reason") == "trim"
    assert bad.get("text_unchanged") is True
    seg = {
        "translated_text": bad["text_uk"],
        "plain_text": bad["text_uk"],
        "text_adaptation_reason": bad["text_adaptation_reason"],
        "audio_strategy_reason": "trim",
        "meaning_fit_status": bad["meaning_fit_status"],
    }
    out = apply_honest_meaning_fit_reasons(seg)
    assert "semantic_paraphrase" not in out["text_adaptation_reason"].lower()
    assert out["text_changed"] is False
