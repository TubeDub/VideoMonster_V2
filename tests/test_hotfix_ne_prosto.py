# -*- coding: utf-8 -*-
"""Hotfix: protect «не просто» (EN not just) from shorten / fillers."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_semantic_shorten_keeps_ne_prosto():
    from engines.meaning_fit.semantic_shorten import _rule_shorten

    src = (
        "Але цей момент у житті Джорджа-молодшого не просто змінив його життя назавжди."
    )
    out = _rule_shorten(src)
    assert out is None or "не просто" in (out or "").lower()
    # Also: filler просто alone may drop, but not after не
    alone = "Він просто пішов додому після довгого дня на роботі."
    alone_out = _rule_shorten(alone)
    if alone_out:
        assert "не просто" not in alone_out.lower() or "просто" not in alone_out.lower()


def test_rule_rewrite_variant_c_keeps_ne_prosto():
    from engines.ai_core.timing_agent.rule_rewrite import shorten_rule

    text = (
        "Але цей момент у житті Джорджа-молодшого не просто змінив його життя назавжди."
    )
    out = shorten_rule(text, tgt_lang="uk", variant="C")
    assert "не просто" in out.lower()


def test_pre_lock_restores_not_just():
    from engines.dsal.pre_lock_polish import restore_not_just_marker

    en = "But this moment in George Jr's life did not just alter George's life forever."
    broken = "Але цей момент у житті Джорджа-молодшого не змінив його життя назавжди"
    out = restore_not_just_marker(broken, original=en)
    assert "не просто змінив" in out.lower()
    # No false positive without not just
    plain = restore_not_just_marker(broken, original="He did not alter his life forever.")
    assert "не просто" not in plain.lower()


def test_naturalizer_tak_dva_tyzhni_and_yiyi():
    from engines.translation_naturalizer import naturalize_uk

    out = naturalize_uk(
        "Так два тижні раніше, коли Джордж повертав, застосувати її до інших речей"
    )
    assert "Так два тижні" not in out
    assert "Два тижні раніше" in out
    assert "застосувати її" not in out
    assert "застосувати це" in out


def test_filler_optimizer_keeps_ne_prosto():
    from engines.smart_segment_optimizer.fillers import iter_filler_removals

    text = (
        "Але цей момент у житті Джорджа-молодшого не просто змінив його життя назавжди"
    )
    steps = iter_filler_removals(text, "uk")
    for step in steps:
        assert step.removed.lower().strip(".,!?") != "просто"
        assert "не просто" in step.text.lower()


def test_hospital_compress_in_semantic_shorten():
    from engines.meaning_fit.semantic_shorten import _rule_shorten

    long = (
        "Через два тижні Джордж-молодший лежав на лікарняному ліжку "
        "у відділенні інтенсивної терапії в місцевій лікарні"
    )
    out = _rule_shorten(long)
    assert out is not None
    assert "реанімації" in out.lower()
    assert len(out) < len(long)
