# -*- coding: utf-8 -*-
"""P0 Language Validation: confidence, entities, no false Language Mismatch."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.language_validation.entities import mask_entities  # noqa: E402
from engines.language_validation.confidence import score_language_confidence  # noqa: E402
from engines.language_validation.service import (  # noqa: E402
    validate_language,
    validate_segments,
)
from engines.language_validation.recovery import apply_recovery_and_revalidate  # noqa: E402
from engines.language_validation.diagnostics import (  # noqa: E402
    write_language_validation_diagnostics,
)
from engines.pipeline_language_gate import validate_segments_target_language  # noqa: E402

UK_CLEAN = (
    "І ось Джордж підійшов до цього перехрестя, де воно було прямо біля його дому, "
    "і він почав повертати, коли почув цей дуже гучний вереск."
)
UK_WITH_NAMES = (
    "Джордж-молодший подав заявку на програму кінематографії в USC, "
    "а потім зустрів Haskell Wexler у Hollywood і зняв Star Wars для George Lucas."
)
UK_WITH_BRANDS = (
    "Він сів у Fiat і поїхав через місто, зняв відео на YouTube і завантажив на Google Drive."
)
UK_LOOP = (
    "Тож за два тижні до того. у той момент, у той момент, у той момент, "
    "у той момент, у той момент, у той момент, у той момент, коли Джордж повертав."
)
RU_CLEAN = "Он подошёл к перекрёстку и начал поворачивать, когда услышал громкий крик."
EN_CLEAN = "He walked to the intersection and started turning when he heard a loud scream."
MIXED_CJK = "Я вагітна. 你怀孕了 і мене викрали."


def test_uk_clean_passes():
    d = validate_language(UK_CLEAN, target_lang="uk", original="He started turning.")
    assert d.ok, d.message
    assert d.detected_lang == "uk"
    assert d.target_confidence >= 0.45
    assert d.category == "pass"


def test_ru_clean_for_ru_target():
    d = validate_language(RU_CLEAN, target_lang="ru", original="He started turning.")
    assert d.ok, d.message
    assert d.detected_lang == "ru"


def test_en_clean_for_en_target():
    d = validate_language(EN_CLEAN, target_lang="en", original="Он начал.")
    assert d.ok, d.message
    assert d.detected_lang == "en"


def test_uk_with_names_not_language_mismatch():
    d = validate_language(
        UK_WITH_NAMES,
        target_lang="uk",
        original="George Jr applied to USC and met Haskell Wexler in Hollywood.",
    )
    assert d.category != "language_mismatch", d.to_dict()
    assert d.detected_lang == "uk" or d.target_confidence >= 0.45
    # Must not hard-fail as Language Mismatch
    assert not (d.hard_fail and d.category == "language_mismatch")


def test_uk_with_brands_not_language_mismatch():
    d = validate_language(
        UK_WITH_BRANDS,
        target_lang="uk",
        original="He got in a Fiat and uploaded to YouTube and Google.",
    )
    assert d.category != "language_mismatch" or not d.hard_fail, d.to_dict()
    assert d.ok or d.category in ("pass", "phrase_loop", "meaning_collapse", "ambiguous")


def test_uk_abbreviations_and_openai_github():
    text = (
        "Він використовував OpenAI API і GitHub SDK, щоб зібрати UI для YouTube, "
        "а потім показав демо в Hollywood для NASA і BBC."
    )
    d = validate_language(
        text,
        target_lang="uk",
        original="He used OpenAI API and GitHub SDK to build a UI for YouTube.",
    )
    assert d.category != "language_mismatch", d.to_dict()
    assert not d.hard_fail or d.category != "language_mismatch"
    assert d.detected_lang == "uk" or d.target_confidence >= 0.45


def test_entity_mask_strips_known():
    masked, ents = mask_entities(
        "George Lucas зняв Star Wars у Hollywood для USC і Fiat"
    )
    assert "⟨E⟩" in masked
    joined = " ".join(ents).lower()
    assert "lucas" in joined or "star wars" in joined or "hollywood" in joined


def test_full_text_not_head_only():
    # English head + Ukrainian body — must see UK mass
    text = (
        "OK Fiat USC. "
        + "Після цього він повернувся додому і зрозумів що життя змінилося назавжди "
        + "тому він вирішив стати режисером і знімати фільми про своє місто."
    )
    sc = score_language_confidence(mask_entities(text)[0], target_lang="uk", masked=True)
    assert sc["scores"]["uk"] > sc["scores"]["en"], sc


def test_uk_equals_uk_never_language_mismatch_code():
    """TZ task 2: expected=uk detected=uk → not Language Mismatch."""
    d = validate_language(
        UK_LOOP,
        target_lang="uk",
        original="Two weeks before that, at that moment when George was turning.",
    )
    assert d.detected_lang == "uk" or d.target_confidence >= 0.45
    assert d.category != "language_mismatch", d.to_dict()
    assert d.code != "language_mismatch"


def test_phrase_loop_recovers():
    segs = [{"text": UK_LOOP, "plain_text": UK_LOOP, "segment_id": "7"}]
    result = apply_recovery_and_revalidate(
        segs,
        source_segments=["Two weeks before that when George was turning."],
        target_lang="uk",
        source_lang="en",
        stage="TEST",
    )
    assert result["failed_hard"] == 0, result
    assert not has_loop(segs[0]["text"])
    hard = validate_segments_target_language(
        segs,
        source_segments=["Two weeks before that when George was turning."],
        target_lang="uk",
        source_lang="en",
        hard_only=True,
    )
    assert hard == [], hard


def has_loop(text: str) -> bool:
    from engines.mt.cross_script_guard import has_phrase_loop

    return has_phrase_loop(text)


def test_real_cjk_leak_still_fails():
    d = validate_language(
        MIXED_CJK,
        target_lang="uk",
        original="你怀孕了",
        source_lang="zh",
    )
    # Must flag somehow (mismatch or collapse) — not silent pass of CJK dump
    assert (not d.ok) or d.category != "pass" or "cjk" in (d.code or "")


def test_diagnostics_files_written():
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        d = validate_language(UK_LOOP, target_lang="uk", original="x", stage="TEST")
        paths = write_language_validation_diagnostics(
            task_id="t_lang_p0",
            app_dir=app,
            stage="TEST",
            decisions=[d.to_dict()],
            recovery={"recovered": 0, "failed_hard": 0, "trace": []},
        )
        for name in (
            "language_validator.log",
            "confidence_scores.json",
            "recovery_trace.json",
            "decision_trace.json",
        ):
            assert name in paths
            assert Path(paths[name]).is_file()
        conf = json.loads(Path(paths["confidence_scores.json"]).read_text(encoding="utf-8"))
        assert conf["task_id"] == "t_lang_p0"


def test_555_style_segments_recover():
    seg8 = (
        "Тож за два тижні до того. у той момент, у той момент, у той момент, "
        "у той момент, у той момент, у той момент, у той момент, коли́ Джордж "
        "повертав. а по́тім щось трапилося"
    )
    seg7 = UK_CLEAN
    rows = [
        {"text": seg7, "plain_text": seg7, "segment_id": "7"},
        {"text": seg8, "plain_text": seg8, "segment_id": "8"},
    ]
    src = [
        "and he started turning when he heard a very loud scream",
        "Two weeks before that at that moment when George was turning something happened",
    ]
    # Soft issues may exist before recovery
    before = validate_segments(
        rows, source_segments=src, target_lang="uk", source_lang="en", stage="TEST"
    )
    assert all(d.category != "language_mismatch" for d in before), before
    result = apply_recovery_and_revalidate(
        rows, source_segments=src, target_lang="uk", source_lang="en", stage="TEST"
    )
    assert result["failed_hard"] == 0, result
    hard = validate_segments_target_language(
        rows, source_segments=src, target_lang="uk", source_lang="en", hard_only=True
    )
    assert hard == [], hard


def main() -> int:
    tests = [
        test_uk_clean_passes,
        test_ru_clean_for_ru_target,
        test_en_clean_for_en_target,
        test_uk_with_names_not_language_mismatch,
        test_uk_with_brands_not_language_mismatch,
        test_uk_abbreviations_and_openai_github,
        test_entity_mask_strips_known,
        test_full_text_not_head_only,
        test_uk_equals_uk_never_language_mismatch_code,
        test_phrase_loop_recovers,
        test_real_cjk_leak_still_fails,
        test_diagnostics_files_written,
        test_555_style_segments_recover,
    ]
    failed = []
    for i, fn in enumerate(tests, 1):
        try:
            fn()
            print(f"[{i}/{len(tests)}] OK {fn.__name__}")
        except Exception as exc:
            print(f"[{i}/{len(tests)}] FAIL {fn.__name__}: {exc}")
            failed.append((fn.__name__, exc))
    if failed:
        print(f"FAILED {len(failed)}/{len(tests)}")
        return 1
    print(f"language_validation_p0 OK ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
