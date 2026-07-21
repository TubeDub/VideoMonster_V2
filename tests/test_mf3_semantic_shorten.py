"""MF3 — SemanticShorten: other words, same meaning; reject chop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.meaning_fit.exceptions import TruncateNotMeaningFitError
from engines.meaning_fit.flags import (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_SHORTEN,
)
from engines.meaning_fit.semantic_shorten import (
    reject_chop_as_shorten,
    semantic_shorten,
)

ROOT = Path(__file__).resolve().parents[1]
GOAT = json.loads((ROOT / "tests" / "fixtures" / "mf0_goat.json").read_text(encoding="utf-8"))

UK2 = "Він швидко біг по довгій вулиці до великого будинку"
UK2_SHORT = "Він швидко біг вулицею до будинку"
UK3 = "Дівчина відкрила стару книжку і почала уважно читати кожну сторінку"


@pytest.fixture
def shorten_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_SHORTEN, "1")
    yield


def test_mf3_goat_shorten(shorten_on):
    res = semantic_shorten(GOAT["long_uk"], GOAT["slot_ms"], force=True)
    assert res.status == "paraphrase_shorten"
    assert res.success is True
    assert res.text_uk == GOAT["short_paraphrase_uk"]
    assert res.text_uk != GOAT["bad_truncate_uk"]
    assert "коза" in res.text_uk.lower()


def test_mf3_two_uk_phrases(shorten_on):
    r2 = semantic_shorten(UK2, slot_ms=2800, force=True)
    assert r2.success is True
    assert r2.text_uk == UK2_SHORT
    r3 = semantic_shorten(UK3, slot_ms=3500, force=True)
    assert r3.success is True
    assert "дівчина" in r3.text_uk.lower()
    assert r3.text_uk != UK3


def test_mf3_chop_rejected(shorten_on):
    with pytest.raises(TruncateNotMeaningFitError):
        reject_chop_as_shorten(GOAT["long_uk"], GOAT["bad_truncate_uk"])


def test_mf3_flag_off_noop(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "0")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_SHORTEN, "0")
    res = semantic_shorten(GOAT["long_uk"], GOAT["slot_ms"])
    assert res.status == "noop"
    assert res.text_uk == GOAT["long_uk"]
