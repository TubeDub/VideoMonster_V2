"""Naturalizer V2 package — entity mask, quality validator, bad MT, mixed language."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("VM_NATURALIZER_V2", "1")
os.environ.setdefault("VM_NATURALIZER_ENTITY_MASK", "1")


def test_entity_mask_restore():
    from engines.naturalizer_v2.entity_tokens import mask_entities, restore_entities

    src = "George Lucas studied at USC and made Star Wars in Hollywood."
    masked, token_map = mask_entities(src)
    assert "George Lucas" not in masked
    assert token_map
    restored, labels = restore_entities(
        masked.replace(list(token_map.keys())[0], "Зоряні війни"),
        token_map,
        original=src,
        tgt_lang="uk",
    )
    assert labels
    assert "Star Wars" in restored or "Зоряні" in restored


def test_bad_mt_detection():
    from engines.naturalizer_v2.bad_patterns import has_bad_mt, detect_bad_mt_patterns

    assert has_bad_mt("отримав очаровательности свого сина")
    assert has_bad_mt("будуть починати війни")
    assert has_bad_mt("Файат")
    hits = detect_bad_mt_patterns("отримав очаров")
    assert any(h["code"] == "uk_calque_charm" for h in hits)


def test_mixed_language_pct():
    from engines.naturalizer_v2.mixed_language import mixed_language_percent

    pct = mixed_language_percent("Так что він пішов додому", tgt_lang="uk")
    assert pct > 3.0


def test_quality_validator_flags_bad_mt():
    from engines.naturalizer_v2.quality_validator import validate_naturalized_quality

    report = validate_naturalized_quality(
        original="He got his son's charm for cars",
        raw_mt="отримав очаровательности свого сина до машин",
        text="отримав очаровательности свого сина до машин",
        src_lang="en",
        tgt_lang="uk",
    )
    assert report.needs_retry
    assert report.score < 70
    assert report.problems


def test_punctuation_cleanup():
    from engines.naturalizer_v2.punctuation import clean_punctuation

    assert clean_punctuation("Привіт..  світ") == "Привіт. світ"
    assert clean_punctuation("  текст  ") == "текст"


def test_v2_rules_fix_calque_without_llm():
    from engines.translation_naturalizer import polish_segment_detailed

    r = polish_segment_detailed(
        "отримав очаровательности свого сина до машин",
        original="He inherited his son's love of cars",
        tgt_lang="uk",
        use_llm=False,
    )
    assert r.quality_score >= 0
    assert "очаров" not in r.text.lower()
    assert r.reasons != ["no_changes"]


def test_v2_no_retry_on_good_text():
    from engines.translation_naturalizer import polish_segment_detailed

    good = "Він успадкував від сина любов до автомобілів."
    r = polish_segment_detailed(good, tgt_lang="uk", use_llm=False)
    assert r.text == good
    assert r.reasons == ["no_changes"]
    assert not r.retried


def test_polish_lines_entity_maps():
    from engines.translation_naturalizer import polish_lines

    src = ["Welcome to Star Wars"]
    maps = [{"TITLE_SW_1": "Star Wars"}]
    raw = ["TITLE_SW_1"]
    meta: list[dict] = []
    out = polish_lines(
        raw,
        source_segments=src,
        tgt_lang="uk",
        use_llm=False,
        entity_maps=maps,
        naturalizer_meta_out=meta,
    )
    assert meta
    assert "Зоряні" in out[0] or "Star Wars" in out[0]


def main() -> int:
    test_entity_mask_restore()
    test_bad_mt_detection()
    test_mixed_language_pct()
    test_quality_validator_flags_bad_mt()
    test_punctuation_cleanup()
    test_v2_rules_fix_calque_without_llm()
    test_v2_no_retry_on_good_text()
    test_polish_lines_entity_maps()
    print("naturalizer v2 full tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
