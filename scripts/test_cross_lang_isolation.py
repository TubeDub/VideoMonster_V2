# -*- coding: utf-8 -*-
"""Cross-language isolation: UK glue must never rewrite RU (and vice versa)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_pre_lock_polish_ru_no_naspravdi():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    src = "In fact, George Jr. had applied to USC."
    ru = "На самом деле Джордж-младший подал заявку в USC."
    out = apply_pre_lock_polish(ru, original=src, tgt_lang="ru")
    assert "Насправді" not in out, out
    assert "молодший" not in out or "младший" in out, out
    assert "Зоряні" not in out, out


def test_pre_lock_polish_uk_still_works():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    src = "George Jr. is better known today as George Lucas"
    uk = "Джордж-молодший. Сьогодні більш відомий як Джордж Лукас"
    out = apply_pre_lock_polish(uk, original=src, tgt_lang="uk")
    assert "молодший. Сьогодні" not in out, out


def test_dsal_expand_skips_ru():
    from engines.dsal.core import _rule_expand_uk

    ru = "Он был там и потом очень быстро ушёл."
    out, stages = _rule_expand_uk(ru, need_ms=2000, tgt_lang="ru")
    assert out == ru
    assert stages == []


def test_dsal_compress_skips_ru():
    from engines.dsal.core import _rule_compress_uk

    ru = "Он на самом деле просто очень долго говорил о машинах."
    out, stages = _rule_compress_uk(
        ru, slot_ms=800, source_hint="He really talked about cars.", tgt_lang="ru"
    )
    assert out == ru
    assert stages == []


def test_mf_shorten_skips_ru():
    from engines.meaning_fit.semantic_shorten import semantic_shorten

    ru = (
        "Итак, Джордж-младший был очень умным ребенком, но он также очень легко "
        "отвлекался и из-за этого ничем серьезно не занимался."
    )
    res = semantic_shorten(ru, slot_ms=500, original_en="So George Jr was smart", tgt_lang="ru")
    assert res.text_uk == ru
    assert res.meta.get("skipped_uk_rules") or res.reason == "non_uk_skip_uk_paraphrase"


def test_compact_phrases_lang_split():
    from engines.semantic_meaning import apply_compact_phrases

    uk = "він не міг не відчути, що боїться"
    ru = "он не мог не почувствовать, что боится"
    uk_out = apply_compact_phrases(uk, target_lang="uk")
    ru_out = apply_compact_phrases(ru, target_lang="ru")
    # UK rewrite must not run on RU input
    mixed = apply_compact_phrases(ru, target_lang="uk")
    assert "почувствовал" in mixed or mixed == ru  # UK table has no RU rows now
    assert "відчув" in uk_out or "відчував" in uk_out
    assert "почувствовал" in ru_out


def test_clause_restore_unknown_lang_no_uk_glue():
    from engines.dsal.clause_coverage import restore_missing_clauses

    # Latin-only text + empty tgt → must not inject Ukrainian
    text = "George had a near-death experience and survived."
    en = "George had a near-death experience and survived."
    out, cov = restore_missing_clauses(text, en, tgt_lang="")
    assert "досвід" not in out.lower(), out
    assert "межі" not in out.lower(), out


def test_ru_near_death_no_uk():
    from engines.dsal.clause_coverage import restore_missing_clauses

    en = "So since his near-death experience, George Jr. had realized his dad was right."
    ru = "Итак, после своего околосмертного опыта Джордж-младший понял, что отец был прав."
    out, _ = restore_missing_clauses(ru + ", досвід на межі смерті", en, tgt_lang="ru")
    assert "досвід" not in out.lower(), out


def main():
    tests = [
        test_pre_lock_polish_ru_no_naspravdi,
        test_pre_lock_polish_uk_still_works,
        test_dsal_expand_skips_ru,
        test_dsal_compress_skips_ru,
        test_mf_shorten_skips_ru,
        test_compact_phrases_lang_split,
        test_clause_restore_unknown_lang_no_uk_glue,
        test_ru_near_death_no_uk,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failed:
        raise SystemExit(failed)
    print(f"OK {len(tests)} tests")


if __name__ == "__main__":
    main()
