"""Regression against live George Lucas Translation Review (2026-07-18)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_canon_repair_seg1_jr_hometown():
    from engines.trh.canon_repair import apply_canon_repair, still_broken_entities

    raw = (
        "18-річному хлопчику ім. Георга Жр. поїхав через рідний міст "
        "на своєму шляху додому."
    )
    out, tickets = apply_canon_repair(
        raw,
        original="An 18-year-old boy named George Jr. drove through his hometown "
        "on his way home for dinner.",
        tgt_lang="uk",
        app_dir=ROOT,
    )
    assert "Жр" not in out
    assert "Георга" not in out or "Джордж" in out
    assert "міст" not in out or "рідне місто" in out.lower()
    assert not still_broken_entities(out, "George Jr. hometown")


def test_canon_repair_seg2_dreading_driving():
    from engines.trh.canon_repair import apply_canon_repair, still_broken_entities

    raw = (
        "Але, як він був водінням, Джордж Жр не може допомогти, але відчувати себе, "
        "як він був дійсно dreading насправді отримати там."
    )
    out, _ = apply_canon_repair(raw, original="George Jr. was driving dreading", tgt_lang="uk")
    assert "водінням" not in out
    assert "dreading" not in out.lower()
    assert "Жр" not in out
    assert not still_broken_entities(out)


def test_canon_repair_seg4_race_track():
    from engines.trh.canon_repair import apply_canon_repair

    raw = "стояв на фінішній прямій на гончарному треку і підняв камеру"
    out, _ = apply_canon_repair(raw, original="race track", tgt_lang="uk")
    assert "гончар" not in out
    assert "гоноч" in out.lower()


def test_canon_repair_seg3_icu():
    from engines.trh.canon_repair import apply_canon_repair

    raw = "Джордж Джер. прокладався в стаціонарному комплексі в місцевій лікарні."
    out, _ = apply_canon_repair(
        raw, original="intensive care unit George Jr.", tgt_lang="uk"
    )
    assert "стаціонарн" not in out.lower()
    assert "Джер" not in out
    assert "інтенсивн" in out.lower() or "реанімац" in out.lower()


def test_canon_repair_usc_not_usa():
    from engines.trh.canon_repair import apply_canon_repair

    raw = "звернувся до USC. я знаю людей у США."
    out, _ = apply_canon_repair(
        raw,
        original="applied to USC. I know people at USC.",
        tgt_lang="uk",
        app_dir=ROOT,
    )
    # When source has USC, США expansion must be corrected
    assert "США" not in out or "USC" in out


def test_canon_repair_seg11_star_wars():
    from engines.trh.canon_repair import apply_canon_repair

    raw = "Георгій Жр, відомий сьогодні, як Джордж Лукас і його кінофраншиза будуть зірвати війни."
    out, _ = apply_canon_repair(
        raw,
        original="George Jr. is better known as George Lucas and star wars.",
        tgt_lang="uk",
        app_dir=ROOT,
    )
    assert "Георгій" not in out
    assert "Жр" not in out
    assert "зірвати війни" not in out.lower()
    assert "Зоряні" in out or "зоряними" in out.lower()
    assert "Джордж" in out


def test_naturalizer_fixes_live_seg1():
    from engines.translation_naturalizer import polish_lines

    raw = (
        "18-річному хлопчику ім. Георга Жр. поїхав через рідний міст "
        "на своєму шляху додому."
    )
    src = (
        "An 18-year-old boy named George Jr. drove through his hometown "
        "on his way home for dinner."
    )
    out = polish_lines(
        [raw], source_segments=[src], tgt_lang="uk", src_lang="en", app_dir=ROOT
    )[0]
    assert out != raw
    assert "Жр" not in out
    assert "dreading" not in out.lower()


def test_fast_qa_rejects_dreading_naturalizer_pass():
    """Seg#2-style: Nat changed but still has en-leak → must FAIL, not silent PASS."""
    from engines.tps.fast_qa import run_fast_qa

    nat = (
        "Але, як він їхав, Джордж-молодший ... dreading насправді отримати там."
    )
    qa = run_fast_qa(
        "George Jr. was driving and dreading",
        nat,
        context={"target_lang": "uk", "raw_mt": "був водінням dreading", "naturalized": nat},
    )
    assert not qa.passed
    assert "en_word_leak" in qa.reason_codes or "entity_breakage" in qa.reason_codes


def test_oversized_seg2_splits():
    from engines.mt.oversized_guard import is_oversized_mt_unit, split_oversized_unit

    mega = (
        "But, as he was driving, George Jr. could not help but feel like he was "
        "really dreading actually getting there. So George Jr. was a very smart kid, "
        "but he also got distracted really easily and because of that, he really had "
        "not pursued anything all that seriously that is except for cars. And at that "
        "point his father actually bought him a small Italian car called the Fiat."
    )
    assert is_oversized_mt_unit(mega)
    assert len(split_oversized_unit(mega)) >= 2
