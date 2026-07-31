# -*- coding: utf-8 -*-
"""Stage 16 — George Jr. Marian UK quality repairs (post-MT)."""

from __future__ import annotations

from engines.mt.glossary_en_uk import apply_uk_marian_repairs, finalize_mt_text
from engines.translation_naturalizer import naturalize_uk


def test_seg2_fiat_italian_and_obsession():
    raw = (
        "Це за винятком машин. але його батько, незважаючи на те, що він був тим, "
        "хто буквально дав йому Фіат, просто не отримав від свого сина одержимості машинами."
    )
    out = finalize_mt_text("en", "uk", raw)
    assert "італійськ" in out.lower()
    assert "Фіат" in out
    assert "не розумів" in out
    assert "не отримав від свого сина" not in out


def test_seg2_live_remt_phrasing():
    raw = (
        "Це не тільки для автомобілів. Батько купив йому маленьку італійську машину Фіат, "
        "але його батько, незважаючи на те, що він дав йому маленький італійський Фіат, "
        "не захопився автомобілями свого сина."
    )
    out = finalize_mt_text("en", "uk", raw)
    assert "за винятком" in out
    assert "хоч і сам подарував" in out
    assert "не розумів" in out
    assert "не захопився" not in out


def test_seg5_crash_phrasing():
    raw = (
        "добре, що це була інша машина, що бігла по дорозі і розбився в машину "
        "Джорджа настільки сильно, що Джорджа Молодшого було викинуто з машини, але він вижив."
    )
    out = apply_uk_marian_repairs(raw)
    assert "мчала" in out or "врізалася" in out
    assert "розбився в машину" not in out
    assert "бігла по дорозі" not in out
    assert "вижив" in out


def test_seg8_application():
    raw = (
        "Насправді Джордж молодший звернувся до престижної кінематографії в університеті "
        "Південної Каліфорнії, але після того, як відпустив заяву, він був упевнений, "
        "що не зможе увійти."
    )
    out = naturalize_uk(raw)
    assert "надіслав заяву" in out
    assert "відпустив заяву" not in out
    assert "подав заявку" in out or "програму з кінематографії" in out
    assert "не зможе увійти" not in out


def test_seg9_usc_and_franchise():
    raw = (
        "коли Хаскел почув це він сказав, що Джордж Я знаю людей в СШ, дозвольте мені "
        "зробити деякі дзвінки і Джордж Молодший отримав би листа від компанії "
        '"Знімання США," і він нещодавно застосувався до USC. Він був кінографом. '
        'його фільм "Зоряні війни" був "Зоряні війни" Джордж молодший. '
        'Сьогодні він відомий як Джордж Лукас, а його фільм "Франгіз" був "Зоряні війни."'
    )
    out = finalize_mt_text("en", "uk", raw)
    assert "людей в USC" in out
    assert "СШ" not in out.replace("USC", "")
    assert "Знімання США" not in out
    assert "кіношколи USC" in out or "зарахування" in out
    assert "застосувався" not in out
    assert "кінооператор" in out
    assert "Франгіз" not in out
    assert "Зоряні війни" in out


def test_seg9_live_tail_cleanup():
    raw = (
        "сфотографувати переможця водія, людина формально представив себе як Хаскелл Векслер "
        "і Джордж молодший розповів Хаскеллу про те, як він нещодавно подав заявку до USC, "
        "щоб спробувати потрапити до своєї кінематографії і коли Хаскелл почув це він сказав, "
        'що «Джордж, я знаю людей в USC, дозвольте мені зробити деякі дзвінки і Джордж Молодший '
        'отримав би листа про зарахування від кіношколи USC" але 13 років пізніше '
        "Джорджа Молодшого створив один з фільмів. Джорджа Молодшого, краще відомий сьогодні "
        'як Джордж Лукас, його фільм "Зоряні війни" був "Зоряні війни" Джордж молодший. '
        "Сьогодні його краще знають як Джорджа Лукаса, а його кінофраншиза, це «Зоряні війни».\""
    )
    out = finalize_mt_text("en", "uk", raw)
    assert "переможного гонщика" in out
    assert "офіційно представився" in out
    assert "їхньої програми" in out
    assert "Джордж Молодший створив" in out
    assert 'був "Зоряні війни"' not in out
    assert "кінофраншиза — це «Зоряні війни»" in out
    assert not out.endswith('"')
