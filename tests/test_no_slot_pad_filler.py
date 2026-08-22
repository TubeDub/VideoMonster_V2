# -*- coding: utf-8 -*-
"""Stage9b — no invented slot-fill pacing (George Jr. RCA).

Fails if expand invents «ось як це було тоді» / «Саме так:» / RU/EN twins.
"""

from __future__ import annotations

import ast
import inspect
import re

from engines.text_slot_fit import (
    _rule_expand_once,
    expand_text_to_slot,
    strip_slot_pad_fillers,
)

_BANNED = (
    "ось як це було тоді",
    "вот как это было тогда",
    "саме так:",
    "именно так:",
    "that is:",
    "that's how it was then",
    "саме тоді",
    "і саме в цей момент",
)


def _contains_banned(text: str) -> str | None:
    low = str(text or "").lower()
    for b in _BANNED:
        if b.lower() in low:
            return b
    return None


def test_strip_slot_pad_fillers():
    dirty = (
        "Через два тижні Джордж лежав у лікарні. "
        "Саме так: через два тижні джордж лежав, "
        "ось як це було тоді, ось як це було тоді — ось як це було тоді."
    )
    clean = strip_slot_pad_fillers(dirty)
    assert _contains_banned(clean) is None
    assert "лікарні" in clean

    dirty_ru = "Он ушёл. Именно так: он ушёл — вот как это было тогда."
    assert _contains_banned(strip_slot_pad_fillers(dirty_ru)) is None


def test_expand_does_not_invent_pacing_filler():
    cases = [
        (
            "Тож він пішов.",
            "uk",
            "So then he kept walking down that long road for a while afterward.",
        ),
        (
            "Итак он пошёл.",
            "ru",
            "So then he kept walking down that long road for a while afterward.",
        ),
        (
            "So he went.",
            "en",
            "So then he kept walking down that long road for a while afterward.",
        ),
    ]
    for short, lang, hint in cases:
        out, _reasons = expand_text_to_slot(short, 7000, lang, source_hint=hint)
        hit = _contains_banned(out)
        assert hit is None, f"expand invented banned pad {hit!r} in {out!r}"
        once = _rule_expand_once(short, lang, source_hint=hint)
        hit2 = _contains_banned(once)
        assert hit2 is None, f"_rule_expand_once invented {hit2!r} in {once!r}"


def test_rule_expand_source_has_no_pad_literals():
    """Static guard: _rule_expand_once must not contain pad string literals."""
    src = inspect.getsource(_rule_expand_once)
    tree = ast.parse(src)
    banned_re = re.compile(
        r"ось як це було тоді|вот как это было тогда|"
        r"Саме так:|Именно так:|That is:",
        re.IGNORECASE,
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not banned_re.search(node.value), (
                f"_rule_expand_once still has pad literal: {node.value!r}"
            )
