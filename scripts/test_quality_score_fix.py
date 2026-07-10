"""Quality score regression tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_bad_mixed_language_not_100():
    from engines.translation_quality_score import compute_quality_score

    score, m = compute_quality_score(
        "Hello world this is a test",
        "Hello world \u0446\u0435 \u0442\u0435\u0441\u0442",
        src_lang="en",
        tgt_lang="uk",
    )
    assert score < 85, f"expected low score, got {score}"
    assert m.get("english_word_pct", 0) > 0


def test_uk_ruism_lowers_score():
    from engines.translation_quality_score import compute_quality_score

    score, m = compute_quality_score(
        "He said that it was fine",
        "\u0412\u0456\u043d \u0441\u043a\u0430\u0437\u0430\u043b \u0447\u0442\u043e \u0446\u0435 \u043d\u043e\u0440\u043c\u0430\u043b\u044c\u043d\u043e",
        src_lang="en",
        tgt_lang="uk",
    )
    assert score < 90, f"ruism should penalize, got {score}"
    assert m.get("uk_ruism_hits")


def test_calque_lowers_score():
    from engines.translation_quality_score import compute_quality_score

    score, _ = compute_quality_score(
        "It makes sense",
        "\u0426\u0435 \u0440\u043e\u0431\u0438\u0442\u044c \u0441\u0435\u043d\u0441",
        src_lang="en",
        tgt_lang="uk",
    )
    assert score < 95, f"calque should penalize, got {score}"


def test_untranslated_whisper_low():
    from engines.translation_quality_score import compute_quality_score

    score, _ = compute_quality_score(
        "Hello there",
        "Hello there",
        src_lang="en",
        tgt_lang="uk",
    )
    assert score <= 40, f"untranslated should be very low, got {score}"


def test_good_uk_can_be_high():
    from engines.translation_quality_score import compute_quality_score

    score, _ = compute_quality_score(
        "Hello",
        "\u041f\u0440\u0438\u0432\u0456\u0442",
        src_lang="en",
        tgt_lang="uk",
    )
    assert score >= 70, f"clean short translation should score well, got {score}"


def main() -> int:
    test_bad_mixed_language_not_100()
    test_uk_ruism_lowers_score()
    test_calque_lowers_score()
    test_untranslated_whisper_low()
    test_good_uk_can_be_high()
    print("quality score fix tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
