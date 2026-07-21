"""MF5 — Score+Select: paraphrase beats truncate; else FIT_FAIL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.meaning_fit.exceptions import TruncateNotMeaningFitError
from engines.meaning_fit.flags import VM_FLAG_MEANING_FIT
from engines.meaning_fit.score_select import score_variant, select_best

ROOT = Path(__file__).resolve().parents[1]
GOAT = json.loads((ROOT / "tests" / "fixtures" / "mf0_goat.json").read_text(encoding="utf-8"))


@pytest.fixture
def mf_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    yield


def test_mf5_paraphrase_beats_truncate(mf_on):
    src = GOAT["long_uk"]
    slot = GOAT["slot_ms"]
    para = score_variant(src, GOAT["short_paraphrase_uk"], slot, method="semantic_shorten")
    with pytest.raises(TruncateNotMeaningFitError):
        score_variant(src, GOAT["bad_truncate_uk"], slot, method="truncate_to_n_chars")

    res = select_best(
        src,
        [
            {"text": GOAT["bad_truncate_uk"], "method": "truncate_to_n_chars"},
            {"text": GOAT["short_paraphrase_uk"], "method": "semantic_shorten"},
        ],
        slot,
    )
    assert res.success is True
    assert res.text_uk == GOAT["short_paraphrase_uk"]
    assert res.reason == "paraphrase_shorten"
    assert para.score > 0.45


def test_mf5_fit_fail_needs_manual(mf_on):
    res = select_best(
        "а б в г ґ д е є ж з и і ї й к л м н о п р с т у ф х ц ч ш щ",
        [{"text": "х", "method": "truncate_to_n_chars"}],
        slot_ms=800,
    )
    assert res.success is False
    assert res.needs_manual is True
    assert res.reason == "fit_failed"
