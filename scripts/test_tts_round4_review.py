# -*- coding: utf-8 -*-
"""Round-4: George Lucas Review bleed / near-death junk / Jr period / debleed."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_debleed_dinner_crash_pair():
    from engines.translation_naturalizer import debleed_adjacent_batch_copies

    blob = (
        "І тому практично кожна вечеря в ці дні перетворювалася на величезну "
        "суперечку між батьком і сином. І ось Джордж, він підійшов до цього "
        "перехрестя, де воно було прямо біля його дому, і він почав повертати, "
        "коли почув цей дуже гучний вереск, а потім усе пішло. Через два тижні "
        "Джордж-молодший лежав на лікарняному ліжку у відділенні інтенсивної "
        "терапії місцевої лікарні."
    )
    src = [
        "And so basically every dinner these days it became this huge argument between father and son.",
        "And so George, he came to this intersection where it was right near his home and he begins making the turn when he hears this really loud screeching sound and then everything went. Two weeks later, George Jr. was laying in a hospital bed.",
    ]
    out = debleed_adjacent_batch_copies(src, [blob, blob])
    assert out[0] != out[1], (out[0][:80], out[1][:80])
    assert "суперечку" in out[0].lower()
    assert "перехрестя" in out[1].lower() or "лікарн" in out[1].lower()
    assert "перехрестя" not in out[0].lower()


def test_debleed_and_not_split_on_ale_vyzyv():
    from engines.translation_naturalizer import debleed_adjacent_batch_copies

    blob = (
        "Тож за два тижні до того, коли Джордж повертав, а потім щось трапилося, "
        "то це було так: по дорозі мчала інша машина й так сильно врізалася в "
        "машину Джорджа, що Джорджа-молодшого викинуло з машини, але він вижив."
    )
    src = [
        "So two weeks earlier when George was making that turn",
        "and then something happened, well what it was is another car came speeding down the road and smashed into George's car so hard that George Jr. was ejected from the car but he had survived.",
    ]
    out = debleed_adjacent_batch_copies(src, [blob, blob])
    # Must NOT leave right as only «але він вижив»
    assert "викинуло" in out[1].lower() or "машини" in out[1].lower(), out[1]
    assert out[0] != out[1] or len(out[0].split()) < len(blob.split())


def test_jr_false_period_before_sogodni():
    from engines.dsal.pre_lock_polish import polish_false_name_period

    src = (
        "Джордж-молодший. Сьогодні більш відомий як Джордж Лукас, "
        "а його франшиза «Зоряні війни»."
    )
    out = polish_false_name_period(src)
    assert "молодший. Сьогодні" not in out, out
    assert "молодший сьогодні" in out.lower() or "молодший Сьогодні" in out


def test_strip_near_death_orphan():
    from engines.dsal.pre_lock_polish import strip_orphan_clause_tails
    from engines.dsal.clause_coverage import compute_clause_coverage, restore_missing_clauses

    uk = (
        "Тож після свого передсмертного досвіду Джордж-молодший зрозумів, "
        "що його тато був правий, досвід на межі смерті"
    )
    out = strip_orphan_clause_tails(
        uk,
        original="So since his near-death experience, George Jr. had realized his dad had been right.",
    )
    assert "досвід на межі смерті" not in out.lower(), out

    cov = compute_clause_coverage(
        "So since his near-death experience, George Jr. had realized his dad had been right.",
        "Тож після свого передсмертного досвіду Джордж-молодший зрозумів, що його тато був правий.",
    )
    assert "досвід на межі смерті" not in cov.missing, cov.missing

    restored, _ = restore_missing_clauses(
        "Тож після свого передсмертного досвіду Джордж-молодший зрозумів, що його тато був правий.",
        "So since his near-death experience, George Jr. had realized his dad had been right.",
    )
    assert "досвід на межі смерті" not in restored.lower(), restored


def test_soft_compress_keeps_discourse():
    from engines.mt.tts_slot_compress import soft_compress_for_slot

    src = (
        "Але його батько, незважаючи на те, що він буквально дав йому Fiat, "
        "він просто не зрозумів одержимість свого сина автомобілями."
    )
    out = soft_compress_for_slot(src, slot_ms=2500, target_lang="uk")
    assert "просто" in out.lower(), out


def test_review_prefers_final_when_tts_truncated():
    from engines.translation_review import _resolve_text_for_tts

    final = (
        "Отже, Джордж-молодший був дуже розумною дитиною, але він також дуже "
        "легко відволікався, і через це він насправді не займався чимось "
        "настільки серйозно."
    )
    tts = (
        "Отже, Джордж-молодший був дуже розумною дитиною, але він також легко "
        "відволікався, і через це він не займався чимось настільки серйозно"
    )
    out = _resolve_text_for_tts(
        {"tts_text": tts},
        {"tts_text": tts},
        final=final,
        tts_synthesized=True,
    )
    assert "насправді" in out.lower(), out


def test_star_wars_debleed_and_deflate():
    from engines.translation_naturalizer import debleed_adjacent_batch_copies
    from engines.mt.cross_script_guard import deflate_phrase_loop, has_phrase_loop
    from engines.dsal.pre_lock_polish import polish_false_name_period

    blob = (
        "Джордж-молодший. Сьогодні більш відомий як Джордж Лукас, а його "
        "франшиза фільму «Зоряні війни». більш відомий сьогодні як Джордж Лукас, "
        "а його франшиза «Зоряні війни»."
    )
    cleaned = polish_false_name_period(blob)
    if has_phrase_loop(cleaned, min_repeats=2):
        cleaned = deflate_phrase_loop(cleaned) or cleaned
    src = [
        "George Jr. is better known today as George Lucas",
        "and his film franchise was Star Wars. better known today as George Lucas and his film franchise was Star Wars.",
    ]
    out = debleed_adjacent_batch_copies(src, [cleaned, cleaned])
    assert "молодший. Сьогодні" not in (out[0] + out[1]), out
    # At least one side should mention Star Wars / Лукас without full twin copy
    assert out[0] != out[1] or not has_phrase_loop(out[0], min_repeats=2)


def main():
    tests = [
        test_debleed_dinner_crash_pair,
        test_debleed_and_not_split_on_ale_vyzyv,
        test_jr_false_period_before_sogodni,
        test_strip_near_death_orphan,
        test_soft_compress_keeps_discourse,
        test_review_prefers_final_when_tts_truncated,
        test_star_wars_debleed_and_deflate,
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
