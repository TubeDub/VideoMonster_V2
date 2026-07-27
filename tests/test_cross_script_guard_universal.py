# -*- coding: utf-8 -*-
"""Universal cross-script MT guards (any language pair)."""

from __future__ import annotations

from engines.mt.cross_script_guard import meaning_collapse, source_script_leak
from engines.pipeline_language_gate import (
    is_critical_language_mismatch,
    validate_segments_target_language,
)
from engines.tps.fast_qa import run_fast_qa
from engines.translation_quality_score import compute_quality_score


ZH = (
    "我们陆下八代单纯 此时单保 有惊呀 你怀孕了 陆下有厚了 要是能一几个男 那就更完美了 "
    "妈 若是我一生的 无论是你 不能想 就怀孕了 我怀孕了 你跟月子前一场义外 一日月前 "
    "我和群辞非同时被绑架 内腕他营喝绑子 所以 这个孩子是绑费的 你跟月子前的意外 这孩子是绑费的"
)
UK_FLOWER = (
    "Ми можемо самі зателефонувати одержувачу і узгодити зручний час і місце "
    "вручення квітів, а якщо необхідно, то збережемо сюрприз."
)
EN_FLOWER = (
    "We can call the recipient ourselves and agree on a convenient time and place "
    "for the delivery of flowers, and if necessary keep the surprise."
)


def test_zh_uk_flower_collapse():
    hit = meaning_collapse(ZH, UK_FLOWER, source_lang="zh", target_lang="uk")
    assert hit is not None


def test_zh_en_flower_collapse():
    hit = meaning_collapse(ZH, EN_FLOWER, source_lang="zh", target_lang="en")
    assert hit is not None


def test_zh_uk_source_leak():
    leak = source_script_leak(ZH, ZH, source_lang="zh", target_lang="uk")
    assert leak is not None
    assert leak["code"] == "source_script_leak"


def test_ar_uk_arabic_leak_blocked():
    ar = "هذا نص عربي طويل بما يكفي للاختبار والتحقق من التسريب"
    bad, code = is_critical_language_mismatch(ar, target_lang="uk", original=ar)
    assert bad
    assert "arabic" in code or "source_script" in code or "cjk" not in code


def test_en_uk_english_leak_blocked():
    text = "that was ejected from the car but he had survived."
    bad, code = is_critical_language_mismatch(text, target_lang="uk", original=text)
    assert bad


def test_uk_en_cyrillic_leak_blocked():
    text = "Джорджа викинули з машини, але він вижив повністю."
    bad, code = is_critical_language_mismatch(text, target_lang="en", original="George was ejected.")
    assert bad
    assert "cyrillic" in code


def test_good_uk_passes():
    text = "Джорджа-молодшого викинули з машини, але він вижив."
    bad, _ = is_critical_language_mismatch(
        text, target_lang="uk", original="George Jr. was ejected from the car."
    )
    assert not bad


def test_fast_qa_and_score_multi_lang():
    qa = run_fast_qa(ZH, UK_FLOWER, context={"source_lang": "zh", "target_lang": "uk"})
    assert not qa.passed
    assert any(
        c in qa.reason_codes for c in ("meaning_collapse", "cjk_meaning_collapse")
    )
    score, metrics = compute_quality_score(ZH, UK_FLOWER, src_lang="zh", tgt_lang="uk")
    assert score < 40.0
    assert metrics.get("meaning_collapse") or metrics.get("cjk_meaning_collapse")


def test_validate_blocks_flower_via_meaning():
    issues = validate_segments_target_language(
        [{"text": UK_FLOWER, "plain_text": UK_FLOWER}],
        source_segments=[ZH],
        target_lang="uk",
        source_lang="zh",
    )
    assert issues
    assert issues[0]["code"] in (
        "meaning_collapse",
        "cjk_in_uk_track",
        "source_script_leak_cjk",
    )
