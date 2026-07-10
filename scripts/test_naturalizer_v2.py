"""Naturalizer v2 — intelligent post-Marian polish tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_tz_duplicates():
    from engines.translation_naturalizer import polish_segment_detailed

    r = polish_segment_detailed("дійсно дійсно", tgt_lang="uk")
    assert r.text == "дійсно"
    assert "fixed_duplicate_words" in r.reasons


def test_tz_calque():
    from engines.translation_naturalizer import polish_segment_detailed

    r = polish_segment_detailed(
        "він не шукав нічого, що є серйозно",
        tgt_lang="uk",
    )
    assert r.text != "він не шукав нічого, що є серйозно"
    assert "fixed_calque" in r.reasons


def test_tz_mixed_language():
    from engines.translation_naturalizer import polish_segment_detailed

    r = polish_segment_detailed("Так что він пішов", tgt_lang="uk")
    assert "Тому що" in r.text or "тому що" in r.text
    assert "fixed_mixed_language" in r.reasons or "fixed_ruism" in r.reasons


def test_tz_proper_nouns():
    from engines.translation_naturalizer import polish_segment_detailed

    src = "Welcome to Star Wars and Fiat"
    r = polish_segment_detailed(
        "Star Wars і Фіат",
        original=src,
        tgt_lang="uk",
    )
    assert "Зоряні війни" in r.text
    assert "Fiat" in r.text
    assert "fixed_named_entities" in r.reasons


def test_tz_ruism_mladshiy():
    from engines.translation_naturalizer import polish_segment_detailed

    r = polish_segment_detailed("Младший інженер", tgt_lang="uk")
    assert "молодший" in r.text.lower()
    assert "fixed_ruism" in r.reasons


def test_tz_word_order():
    from engines.translation_naturalizer import polish_segment_detailed

    r = polish_segment_detailed(
        "коли він був прямо поруч з його домом",
        tgt_lang="uk",
    )
    assert "майже біля" in r.text
    assert "fixed_word_order" in r.reasons


def test_tz_pronoun_dup():
    from engines.translation_naturalizer import polish_segment_detailed

    r = polish_segment_detailed("він він пішов", tgt_lang="uk")
    assert r.text.lower().count("він") == 1
    assert "fixed_duplicate_words" in r.reasons


def test_no_changes_on_good_text():
    from engines.translation_naturalizer import polish_segment_detailed

    good = "Привіт, як у тебе справи?"
    r = polish_segment_detailed(good, tgt_lang="uk")
    assert r.text == good
    assert r.reasons == ["no_changes"]


def test_polish_lines_reasons_out():
    from engines.translation_naturalizer import polish_lines

    reasons: list[list[str]] = []
    raw = ["Він младший", "Привіт, як справи?"]
    out = polish_lines(
        raw,
        tgt_lang="uk",
        use_llm=False,
        naturalizer_reasons_out=reasons,
    )
    assert out[0] != raw[0]
    assert "fixed_ruism" in reasons[0]
    assert reasons[1] == ["no_changes"]


def main() -> int:
    test_tz_duplicates()
    test_tz_calque()
    test_tz_mixed_language()
    test_tz_proper_nouns()
    test_tz_ruism_mladshiy()
    test_tz_word_order()
    test_tz_pronoun_dup()
    test_no_changes_on_good_text()
    test_polish_lines_reasons_out()
    print("naturalizer v2 tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
