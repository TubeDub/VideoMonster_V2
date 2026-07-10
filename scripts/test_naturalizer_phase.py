"""Naturalizer always applies + proper nouns v2."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_polish_always_fixes_ruism():
    from engines.translation_naturalizer import polish_lines

    raw = ["Він ще не знає, что робити"]
    out = polish_lines(raw, tgt_lang="uk", use_llm=False, quality_scores=[50.0])
    assert out[0] != raw[0]
    assert "що" in out[0].lower()
    assert "что" not in out[0].lower()


def test_star_wars_preferred():
    from engines.proper_nouns_dict import apply_preferred_translations

    src = "Welcome to Star Wars universe"
    bad = "Ласкаво просимо до Star Wars"
    out = apply_preferred_translations(src, bad)
    assert "Зоряні війни" in out


def test_fiat_latin():
    from engines.proper_nouns_dict import restore_never_translate_tokens

    src = "He drives a Fiat"
    bad = "Він їде на Фіат"
    out = restore_never_translate_tokens(src, bad)
    assert "Fiat" in out
    assert "Фіат" not in out


def main() -> int:
    test_polish_always_fixes_ruism()
    test_star_wars_preferred()
    test_fiat_latin()
    print("naturalizer phase tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
