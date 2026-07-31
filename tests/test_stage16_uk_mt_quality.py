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
