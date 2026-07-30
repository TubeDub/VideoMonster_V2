# -*- coding: utf-8 -*-
"""Stage 14 — superseded by 14b; same post-MT-only checks."""

from __future__ import annotations

from engines.mt.glossary_en_uk import (
    apply_post_mt_glossary_fixes,
    contains_glossary_garbage,
    finalize_mt_text,
    protect_glossary,
    restore_glossary,
    strip_glossary_placeholders,
    translate_with_glossary_protect,
)


def test_protect_is_noop_simple():
    src = (
        "George Jr. loved Star Wars and met George Lucas and Haskell Wexler "
        "at USC after buying a Fiat."
    )
    protected, mapping = protect_glossary(src)
    assert protected == src
    assert mapping == []
    assert "__GLOS" not in protected
    assert restore_glossary(protected, mapping) == src


def test_post_mt_replaces_star_wars_and_names():
    raw = (
        "George Jr. drove a Fiat. Later George Lucas made Star Wars "
        "and Haskell Wexler helped at USC in Hollywood."
    )
    out = apply_post_mt_glossary_fixes(raw)
    assert "Джордж Молодший" in out
    assert "Фіат" in out
    assert "Зоряні війни" in out
    assert "Джордж Лукас" in out
    assert "Хаскелл Векслер" in out
    assert "Голлівуд" in out
    assert "Star Wars" not in out
    assert "Fiat" not in out


def test_strip_removes_mangled_glos_tokens():
    dirty = "текст __GLOS__000_ _GLOS_001 __GLOS_XY }USC ведьг0] G000 кінець"
    clean = strip_glossary_placeholders(dirty)
    assert "__GLOS" not in clean
    assert "_GLOS" not in clean
    assert "}USC" not in clean
    assert "ведьг" not in clean.lower()
    assert "G000" not in clean
    assert not contains_glossary_garbage(clean)


def test_finalize_strips_then_applies_glossary():
    dirty = "George Jr. bought a Fiat __GLOS_000__ }1 Star Wars"
    clean = finalize_mt_text("en", "uk", dirty)
    assert "Джордж Молодший" in clean
    assert "Фіат" in clean
    assert "Зоряні війни" in clean
    assert "__GLOS" not in clean
    assert not contains_glossary_garbage(clean)


def test_translate_raw_then_post_glossary():
    def fake(text: str) -> str:
        return text.replace("drove", "їхав")

    out = translate_with_glossary_protect(
        "George Jr. drove a Fiat to see Star Wars.",
        fake,
    )
    assert "Джордж Молодший" in out
    assert "Фіат" in out
    assert "Зоряні війни" in out
    assert "__GLOS" not in out
