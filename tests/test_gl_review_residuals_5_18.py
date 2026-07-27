# -*- coding: utf-8 -*-
"""GL Review residuals: father-son ти, racetrack, obsession case, franchise, Haskell walk."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.translation_naturalizer import naturalize_uk, polish_lines


def test_father_son_real_job_uses_ty_not_vy():
    en = (
        "Like why aren't you able to take that focus and apply it to other things "
        "that will get you a real job?"
    )
    raw = (
        "Наприклад, чому ви не можете зосередитися на цьому й застосувати його "
        "до інших речей, які дадуть вам справжню роботу?"
    )
    out = polish_lines(
        [raw],
        source_segments=[en],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "чому ви" not in out.lower()
    assert "чому ти не можеш" in out.lower()
    assert "вам справжню" not in out.lower()
    assert "тобі справжню" in out.lower() or "тобі" in out.lower()


def test_racetrack_not_hippodrome():
    raw = (
        "Через два роки Джордж-молодший, який до цього моменту повністю оговтався "
        "від травм, стояв на фініші на іподромі й підняв камеру."
    )
    out = naturalize_uk(raw)
    assert "іподром" not in out.lower()
    assert "гоночн" in out.lower()
    assert "оговтався" not in out.lower()


def test_obsession_genitive_and_race_cars():
    assert "одержимості" in naturalize_uk(
        "він просто не зрозумів одержимість свого сина автомобілями"
    )
    assert "автоперегонах" in naturalize_uk(
        "більше не хоче брати участь у перегонах автомобілів"
    )


def test_haskell_walk_dedupe_and_franchise_identity():
    raw14 = (
        "Джордж-молодший підійшов до подіуму, щоб сфотографувати водія-переможця, "
        "але коли він підійшов до нього, цей чоловік середнього віку підійшов до "
        "нього та просто запитав Джорджа-молодшого про його фотографію, а потім у "
        "якийсь момент чоловік фактично офіційно представився як Хаскелл Векслер."
    )
    out14 = polish_lines(
        [raw14],
        source_segments=[
            "Now, George Jr. walked over to the podium to take some photos of the "
            "winning driver but as he walked over there, this middle-aged man came "
            "up beside him and just asked George Jr. about his photography and then "
            "at some point the man actually formally introduced himself as Haskell Wexler."
        ],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert out14.lower().count("підійшов до нього") <= 1
    assert "фактично офіційно" not in out14.lower()

    raw18 = (
        "Джордж-молодший сьогодні більш відомий як Джордж Лукас, "
        "а його франшиза фільму «Зоряні війни»."
    )
    out18 = naturalize_uk(raw18)
    assert "франшиза фільму" not in out18
    assert "кінофраншиза — це «Зоряні війни»" in out18


def test_dread_compact_not_afraid_to_get_there():
    raw = (
        "Але коли він їхав, Джордж-молодший відчув, що він справді боїться "
        "потрапити туди."
    )
    out = polish_lines(
        [raw],
        source_segments=[
            "But as he was driving, George Jr. could not help but feel like he was "
            "really dreading actually getting there."
        ],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "боїться потрапити туди" not in out
    assert "не хочеться" in out or "не хотілося" in out
