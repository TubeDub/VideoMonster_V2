# -*- coding: utf-8 -*-
"""CJK → UK meaning collapse / source leak / flower hallucination."""

from __future__ import annotations

from engines.mt.cjk_meaning import meaning_collapse_zh_to_cyrillic
from engines.mt.dirty_mt import compute_dirty_mt_score
from engines.tps.fast_qa import run_fast_qa
from engines.translation_quality_score import compute_quality_score


ZH_PREGNANCY_KIDNAP = (
    "我们陆下八代单纯 此时单保 有惊呀 你怀孕了 陆下有厚了 要是能一几个男 那就更完美了 "
    "妈 若是我一生的 无论是你 不能想 就怀孕了 我怀孕了 你跟月子前一场义外 一日月前 "
    "我和群辞非同时被绑架 内腕他营喝绑子 所以 这个孩子是绑费的 你跟月子前的意外 这孩子是绑费的"
)

UK_FLOWER = (
    "Ми можемо самі зателефонувати одержувачу і узгодити зручний час і місце "
    "вручення квітів, а якщо необхідно, то збережемо сюрприз."
)

UK_WAFFLE = (
    "І він має на увазі, що він використовується для того, щоб показати, "
    "що він — це просто щось, що може бути таким, як ми маємо."
)


def test_flower_delivery_is_cjk_collapse():
    hit = meaning_collapse_zh_to_cyrillic(
        ZH_PREGNANCY_KIDNAP, UK_FLOWER, source_lang="zh", target_lang="uk"
    )
    assert hit is not None
    assert hit["code"] == "cjk_meaning_collapse"


def test_source_script_leak_dirty():
    dirty = compute_dirty_mt_score(
        ZH_PREGNANCY_KIDNAP, ZH_PREGNANCY_KIDNAP, tgt_lang="uk"
    )
    assert dirty.dirty is True
    assert "source_script_leak" in dirty.reasons


def test_fast_qa_fails_flower_and_leak():
    qa_flower = run_fast_qa(
        ZH_PREGNANCY_KIDNAP,
        UK_FLOWER,
        context={"source_lang": "zh", "target_lang": "uk"},
    )
    assert not qa_flower.passed
    assert "cjk_meaning_collapse" in qa_flower.reason_codes

    qa_leak = run_fast_qa(
        ZH_PREGNANCY_KIDNAP,
        ZH_PREGNANCY_KIDNAP,
        context={"source_lang": "zh", "target_lang": "uk"},
    )
    assert not qa_leak.passed
    assert "source_script_leak" in qa_leak.reason_codes


def test_quality_score_caps_flower():
    score, metrics = compute_quality_score(
        ZH_PREGNANCY_KIDNAP, UK_FLOWER, src_lang="zh", tgt_lang="uk"
    )
    assert metrics.get("cjk_meaning_collapse") is True
    assert score < 40.0
    from engines.quality_score_v2 import compute_quality_score_v2

    score2, _ = compute_quality_score_v2(
        ZH_PREGNANCY_KIDNAP, UK_FLOWER, src_lang="zh", tgt_lang="uk"
    )
    assert score2 <= 18.0


def test_waffle_still_detected():
    hit = meaning_collapse_zh_to_cyrillic(
        ZH_PREGNANCY_KIDNAP, UK_WAFFLE, source_lang="zh", target_lang="uk"
    )
    assert hit is not None
