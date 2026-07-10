"""Proper nouns dictionary tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_find_fiat_in_source():
    from engines.proper_nouns_dict import find_tokens_in_source

    hits = find_tokens_in_source("He bought a Fiat car")
    assert any(h.lower() == "fiat" for h in hits)


def test_restore_fiat():
    from engines.proper_nouns_dict import restore_never_translate_tokens

    src = "He bought a Fiat car"
    bad = "\u0412\u0456\u043d \u043a\u0443\u043f\u0438\u0432 \u0424\u0456\u0430\u0442"
    out = restore_never_translate_tokens(src, bad)
    assert "Fiat" in out


def test_wrong_brand_detection():
    from engines.proper_nouns_dict import wrong_phonetic_brand_hits

    hits = wrong_phonetic_brand_hits(
        "Hollywood studio",
        "\u0413\u043e\u043b\u043b\u0456\u0432\u0443\u0434\u0441\u044c\u043a\u0430 \u0441\u0442\u0443\u0434\u0456\u044f",
    )
    assert "Hollywood" in hits


def test_extract_preserved_includes_dict():
    from engines.translation_quality import extract_preserved_tokens

    tokens = extract_preserved_tokens("Fiat and USC in Hollywood")
    joined = " ".join(tokens).lower()
    assert "fiat" in joined
    assert "usc" in joined


def main() -> int:
    test_find_fiat_in_source()
    test_restore_fiat()
    test_wrong_brand_detection()
    test_extract_preserved_includes_dict()
    print("proper nouns dict tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
