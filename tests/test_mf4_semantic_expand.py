"""MF4 — SemanticExpand: slightly longer, no filler."""

from __future__ import annotations

import pytest

from engines.meaning_fit.flags import (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_EXPAND,
)
from engines.meaning_fit.semantic_expand import semantic_expand


@pytest.fixture
def expand_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_EXPAND, "1")
    yield


def test_mf4_expand_too_short(expand_on):
    res = semantic_expand("Коза паслась", slot_ms=2500, force=True)
    assert res.status == "paraphrase_expand"
    assert res.text_uk != "Коза паслась"
    assert "еее" not in res.text_uk.lower()
    assert res.predicted_ms and res.predicted_ms > 0


def test_mf4_no_filler_repeat(expand_on):
    res = semantic_expand("Він біг", slot_ms=2200, force=True)
    assert res.success or res.status == "paraphrase_expand"
    words = res.text_uk.lower().split()
    for i in range(1, len(words)):
        assert not (words[i] == words[i - 1] and len(words[i]) > 2)


def test_mf4_flag_off(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "0")
    res = semantic_expand("Коза паслась", slot_ms=2500)
    assert res.status == "noop"
