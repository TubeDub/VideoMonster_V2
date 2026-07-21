"""MF2 — DurationPredictor: TOO_LONG / TOO_SHORT / OK. No shorten/expand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.meaning_fit.duration_predictor import (
    classify_vs_slot,
    duration_gate,
    predict_ms,
    predict_vs_slot,
)
from engines.meaning_fit.flags import VM_FLAG_MEANING_FIT

ROOT = Path(__file__).resolve().parents[1]
GOAT = json.loads((ROOT / "tests" / "fixtures" / "mf0_goat.json").read_text(encoding="utf-8"))


@pytest.fixture
def mf_off(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "0")
    monkeypatch.setenv("VM_FLAG_MEANING_FIT_BEFORE_LOCK", "0")
    yield


@pytest.fixture
def mf_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    yield


def test_mf2_long_uk_too_long(mf_on):
    pred = predict_vs_slot(GOAT["long_uk"], GOAT["slot_ms"])
    assert pred.verdict == "TOO_LONG"
    assert pred.predicted_ms > GOAT["slot_ms"]


def test_mf2_paraphrase_ok(mf_on):
    pred = predict_vs_slot(GOAT["short_paraphrase_uk"], GOAT["slot_ms"])
    assert pred.verdict == "OK"
    assert predict_ms(GOAT["short_paraphrase_uk"]) == pred.predicted_ms


def test_mf2_flag_off_does_not_affect_pipeline(mf_off):
    gate = duration_gate(GOAT["long_uk"], GOAT["slot_ms"])
    assert gate["enabled"] is False
    assert gate["affects_pipeline"] is False
    assert gate["verdict"] == "UNKNOWN"
    assert gate["raw_verdict"] == "TOO_LONG"


def test_mf2_classify_helpers():
    assert classify_vs_slot(4000, 2500) == "TOO_LONG"
    assert classify_vs_slot(1000, 2500) == "TOO_SHORT"
    assert classify_vs_slot(2000, 2500) == "OK"
