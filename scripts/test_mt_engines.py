"""MT engine module tests — Task №15."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.mt.registry import get_registry, translate_with_best_engine
from engines.translation_quality_score import compute_quality_score, MIN_ACCEPT_QUALITY


def test_qe_rejects_argos_bad_uk() -> None:
    """Production QE must reject Argos-style name mangling."""
    good, _ = compute_quality_score(
        "My name is John.",
        "Мене звуть Джон.",
        src_lang="en",
        tgt_lang="uk",
    )
    bad, bad_m = compute_quality_score(
        "My name is John.",
        "Ім'я Івана",
        src_lang="en",
        tgt_lang="uk",
    )
    assert good > bad, f"good={good} bad={bad}"
    assert bad_m.get("intro_pattern_penalty", 0) > 0 or bad < MIN_ACCEPT_QUALITY


def test_registry_has_engines() -> None:
    engines = get_registry()
    ids = {e.id for e in engines}
    assert "argos" in ids or "marian" in ids or "deep" in ids


def test_en_uk_marian_quality() -> None:
    """EN→UK must not produce Argos-style garbage when Marian is available."""
    text, meta = translate_with_best_engine(
        "My name is John.",
        "en",
        "uk",
        app_dir=ROOT,
    )
    assert meta.get("engine") in ("marian", "nllb", "deep")
    score, metrics = compute_quality_score(
        "My name is John.", text, src_lang="en", tgt_lang="uk"
    )
    assert score >= MIN_ACCEPT_QUALITY, f"score={score} text={text!r} metrics={metrics}"
    assert "Ім'я Івана" not in text


def test_translate_interface() -> None:
    for eng in get_registry():
        if not eng.supports_pair("en", "ru"):
            continue
        result = eng.translate("Hello world.", "en", "ru")
        assert result.text.strip()
        assert result.engine_id == eng.id
        break


def test_quality_categories() -> None:
    cases = [
        ("Hey, are you coming tonight?", "en", "uk"),
        ("It was a piece of cake.", "en", "de"),
        ("NASA launched a new satellite.", "en", "ru"),
    ]
    for text, src, tgt in cases:
        out, meta = translate_with_best_engine(text, src, tgt, app_dir=ROOT)
        score, _ = compute_quality_score(text, out, src_lang=src, tgt_lang=tgt)
        assert out.strip(), f"empty for {text}"
        assert score > 0, f"zero score for {text}"


def main() -> int:
    test_qe_rejects_argos_bad_uk()
    test_registry_has_engines()
    test_translate_interface()
    test_quality_categories()
    test_en_uk_marian_quality()
    print("mt engine tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
