# -*- coding: utf-8 -*-
"""Round-5: en→ru Review — debleed, no UK orphan, Hollywood, near-death cover."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_ru_debleed_dinner_crash():
    from engines.translation_naturalizer import debleed_adjacent_batch_copies

    blob = (
        "И поэтому практически каждый ужин в наши дни становился огромным спором "
        "между отцом и сыном. И вот Джордж, он подошел к этому перекрестку, где он "
        "был прямо возле его дома, и он начал поворачивать. Две недели спустя "
        "Джордж-младший лежал на больничной койке."
    )
    src = [
        "And so basically every dinner these days it became this huge argument between father and son.",
        "And so George, he came to this intersection where it was right near his home and he begins making the turn. Two weeks later, George Jr. was laying in a hospital bed.",
    ]
    out = debleed_adjacent_batch_copies(src, [blob, blob])
    assert out[0] != out[1], (out[0][:60], out[1][:60])
    assert "спором" in out[0].lower()
    assert "перекрестк" in out[1].lower() or "больнич" in out[1].lower()
    assert "перекрестк" not in out[0].lower()


def test_ru_debleed_seg1_2():
    from engines.translation_naturalizer import debleed_adjacent_batch_copies

    blob = (
        "18-летний мальчик по имени Джордж-младший проезжал через свой родной город "
        "по пути домой на ужин. Но пока он вел машину, Джордж-младший не мог не "
        "чувствовать, что действительно боится добраться туда."
    )
    src = [
        "An 18-year-old boy named George Jr. drove through his hometown on his way home for dinner.",
        "But as he was driving, George Jr. could not help but feel like he was really dreading actually getting there.",
    ]
    out = debleed_adjacent_batch_copies(src, [blob, blob])
    assert out[0] != out[1]
    assert "ужин" in out[0].lower()
    assert "вел машину" in out[1].lower() or "боится" in out[1].lower()
    assert "вел машину" not in out[0].lower()


def test_ru_near_death_no_uk_orphan():
    from engines.dsal.clause_coverage import (
        compute_clause_coverage,
        restore_missing_clauses,
        strip_cross_lang_clause_orphans,
    )

    en = "So since his near-death experience, George Jr. had realized his dad had been right."
    ru = (
        "Итак, после своего околосмертного опыта Джордж-младший понял, "
        "что на самом деле его отец был в некотором роде прав."
    )
    cov = compute_clause_coverage(en, ru, tgt_lang="ru")
    assert "досвід" not in " ".join(cov.missing).lower(), cov.missing
    assert "опыт на грани смерти" not in cov.missing, cov.missing

    restored, cov2 = restore_missing_clauses(ru, en, tgt_lang="ru")
    assert "досвід" not in restored.lower(), restored
    assert "на межі" not in restored.lower(), restored

    polluted = ru + ", досвід на межі смерті"
    clean = strip_cross_lang_clause_orphans(polluted)
    assert "досвід" not in clean.lower(), clean
    assert "околосмертного" in clean.lower()


def test_ru_hollywood_fix():
    from engines.translation_naturalizer import naturalize_ru

    out = naturalize_ru(
        "он был кинематографистом в Голлівуде и рассказал об этом"
    )
    assert "Голлівуд" not in out, out
    assert "Голливуд" in out, out


def test_uk_near_death_still_covered():
    from engines.dsal.clause_coverage import compute_clause_coverage, restore_missing_clauses

    en = "So since his near-death experience, George Jr. had realized his dad had been right."
    uk = "Тож після свого передсмертного досвіду Джордж-молодший зрозумів, що його тато був правий."
    cov = compute_clause_coverage(en, uk, tgt_lang="uk")
    assert "досвід на межі смерті" not in cov.missing, cov.missing
    restored, _ = restore_missing_clauses(uk, en, tgt_lang="uk")
    assert "досвід на межі смерті" not in restored.lower()


def main():
    tests = [
        test_ru_debleed_dinner_crash,
        test_ru_debleed_seg1_2,
        test_ru_near_death_no_uk_orphan,
        test_ru_hollywood_fix,
        test_uk_near_death_still_covered,
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
