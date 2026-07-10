"""Unit checks for universal translation quality guards."""
from __future__ import annotations

import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.translation_quality import (
    apply_translation_quality_pass,
    extract_abbreviations,
    extract_proper_nouns,
    extract_preserved_tokens,
    is_nonsense_text,
    keep_if_not_worse,
    preserve_proper_nouns,
    run_quality_validation,
    segment_quality_warnings,
)


def test_preserve_proper_nouns_no_mutation() -> None:
    src = "John went to Paris"
    tr = "Он поехал во Францию"
    out = preserve_proper_nouns(src, tr)
    assert out == tr


def test_discourse_not_proper_noun() -> None:
    for word in ("Two", "Now", "Instead", "Like", "Well", "So", "But", "And"):
        assert word not in extract_proper_nouns(f"{word} years later")
    assert "George" in extract_proper_nouns("George Jr. drove home")


def test_abbrev_no_lowercase_words() -> None:
    text = "Like, why did you change the name?"
    abb = extract_abbreviations(text)
    assert "did" not in abb and "like" not in abb and "name" not in abb


def test_no_english_append() -> None:
    src = ["The US versions like a top 10 show of all time."]
    tr = ["Американские версии любят топ-10 шоу всех времён."]
    out = apply_translation_quality_pass(src, tr)
    assert out[0] == tr[0]
    assert "versions" not in out[0].lower() or "верси" in out[0].lower()


def test_consistency_pass_passthrough() -> None:
    src = ["Elon spoke", "Then Elon left"]
    tr = ["Илон выступил", "Потом он ушёл"]
    out = apply_translation_quality_pass(src, tr)
    assert out == tr


def test_nonsense() -> None:
    assert is_nonsense_text("aaaaaaaabbbbbbbb")
    assert not is_nonsense_text("Hello world")


def test_warnings_structure() -> None:
    w = segment_quality_warnings(
        original="John said hello",
        raw="Он сказал привет",
        naturalized="Он сказал привет",
        final="Он сказал привет",
        tts_text="Он сказал привет",
        source_lang="en",
        target_lang="ru",
    )
    codes = {x["code"] for x in w}
    assert "preserved_token" in codes


def test_abbreviations() -> None:
    abb = extract_abbreviations("Dr. Smith Jr. at USC in the U.S.")
    assert any("USC" in a for a in abb)
    assert any("Jr" in a for a in abb)
    assert any(re.search(r"U\.?\s?S\.?", a, re.I) for a in abb) or "US" in abb


def test_keep_if_not_worse() -> None:
    good = "Американские версии любят топ-10 шоу."
    bad = "Американские версии, versions, like, top"
    assert keep_if_not_worse(good, bad) == good
    assert keep_if_not_worse("", "Привет") == "Привет"
    assert keep_if_not_worse("Привет", "") == "Привет"


def test_quality_validation_readonly() -> None:
    src = ["John went to Paris"]
    tr = ["Он поехал во Францию"]
    texts, warnings = run_quality_validation(src, tr, src_lang="en", tgt_lang="ru", raw_segments=tr)
    assert texts == tr
    assert isinstance(warnings, list)
    assert len(warnings) == 1


def test_preserved_tokens_real_names() -> None:
    toks = extract_preserved_tokens("John Smith Jr. studied at USC")
    assert any("John" in t or "Smith" in t for t in toks)
    assert any("USC" in t for t in toks)


def test_missing_preserved_no_crash_on_compound_names() -> None:
    """Regression: George Lucas token must not IndexError when George not in names dict."""
    from engines.translation_quality import missing_preserved_tokens

    src = (
        "go on to become part of the most successful movie franchise of all time. "
        "George Jr. is better known today as George Lucas and his film franchise will star wars."
    )
    tr = (
        "Його фільм стане частиною найуспішнішої кінофраншизи. "
        "Джордж молодший сьогодні більш відомий як Джордж Лукас, а його кінофраншиза — Зоряні війни."
    )
    missing = missing_preserved_tokens(src, tr)
    assert "George Lucas" not in missing
    w = segment_quality_warnings(
        original=src,
        raw=tr,
        naturalized=tr,
        final=tr,
        tts_text=tr,
        source_lang="en",
        target_lang="uk",
    )
    assert isinstance(w, list)


def main() -> int:
    test_preserve_proper_nouns_no_mutation()
    test_discourse_not_proper_noun()
    test_abbrev_no_lowercase_words()
    test_no_english_append()
    test_consistency_pass_passthrough()
    test_nonsense()
    test_warnings_structure()
    test_abbreviations()
    test_keep_if_not_worse()
    test_quality_validation_readonly()
    test_preserved_tokens_real_names()
    test_missing_preserved_no_crash_on_compound_names()
    print("translation quality guards: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
