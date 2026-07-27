# -*- coding: utf-8 -*-
"""zh→uk production path: ASR correct, gloss rescue, gate, TTS compress."""

from __future__ import annotations

from engines.mt.cross_script_guard import meaning_collapse
from engines.mt.tts_slot_compress import soft_compress_for_slot
from engines.mt.zh_asr_correct import (
    correct_zh_asr_segments,
    correct_zh_asr_text,
    is_cjk_turn_break,
)
from engines.mt.zh_drama_gloss import lookup_exact_turn, try_offline_gloss_rescue
from engines.pipeline_language_gate import (
    salvage_collapsed_segment_text,
    validate_segments_target_language,
)
from engines.segment_merger import merge_stt_segments
from engines.translation_validation import _retry_candidate_bad


def test_asr_fixes_homophones():
    assert correct_zh_asr_text("我们陆家八代单纯") == "我们陆家八代单传"
    assert correct_zh_asr_text("自私担保") == "子嗣难保"
    assert correct_zh_asr_text("要是能一举得难") == "要是能一举得男"
    assert "绑匪" in correct_zh_asr_text("这个孩子是绑费的")


def test_asr_segments_batch():
    raw = ["我们陆家八代单纯", "自私担保", "你怀孕了"]
    fixed = correct_zh_asr_segments(raw, language="zh")
    assert fixed[0].endswith("单传")
    assert fixed[1] == "子嗣难保"
    assert fixed[2] == "你怀孕了"


def test_turn_break_markers():
    assert is_cjk_turn_break("妈")
    assert is_cjk_turn_break("我怀孕了")
    assert not is_cjk_turn_break("那就更完美了")


def test_merge_respects_turn_breaks():
    lines = [
        "我们陆家八代单传",
        "子嗣难保",
        "如今啊",
        "你怀孕了",
        "妈",
        "这是我一生的",
    ]
    # 1s cues with tiny gaps, then 妈 after a gap still must break
    timing = [
        {"start": 590, "end": 2590},
        {"start": 2590, "end": 3590},
        {"start": 3590, "end": 4590},
        {"start": 4590, "end": 5590},
        {"start": 11590, "end": 14030},
        {"start": 14030, "end": 15030},
    ]
    merged, _ = merge_stt_segments(lines, timing)
    assert any(t == "妈" or t.startswith("妈") for t in merged)
    # 妈 must not be glued into pregnancy block
    assert not any("怀孕" in t and "妈" in t for t in merged)


def test_exact_gloss_pregnancy():
    uk = lookup_exact_turn("你怀孕了", tgt_lang="uk")
    assert uk is not None
    assert "вагітн" in uk.lower()
    hit = try_offline_gloss_rescue("我怀孕了", "Ти народила хлопчика.", tgt_lang="uk")
    assert hit is not None
    assert "вагітн" in hit["text"].lower()


def test_salvage_uses_offline_gloss():
    text, method = salvage_collapsed_segment_text(
        text="Доставка квітів по Києву.",
        original="你怀孕了",
        approved="",
        target_lang="uk",
        source_lang="zh",
    )
    assert text is not None
    assert "вагітн" in text.lower()
    assert "gloss" in method or "llm" in method or method.endswith("_ok")


def test_retry_rejects_flower_waffle():
    bad, code = _retry_candidate_bad(
        "你怀孕了 陆家有后了",
        "Ми можемо самі зателефонувати одержувачу і узгодити вручення квітів.",
        source_lang="zh",
        target_lang="uk",
    )
    assert bad
    assert code in ("meaning_collapse",) or "cjk" in code or "leak" in code


def test_curated_uk_passes_gate():
    src = [
        "我们陆家八代单传",
        "你怀孕了",
        "喜欢哥哥",
        "我怀孕了",
        "这个孩子是绑匪的",
    ]
    uk = [
        lookup_exact_turn(s, tgt_lang="uk") or s for s in src
    ]
    issues = validate_segments_target_language(
        [{"text": t, "plain_text": t} for t in uk],
        source_segments=src,
        target_lang="uk",
        source_lang="zh",
    )
    assert issues == []


def test_birth_flip_still_collapse():
    hit = meaning_collapse(
        "你怀孕了",
        "Ти народила хлопчика.",
        source_lang="zh",
        target_lang="uk",
    )
    assert hit is not None


def test_soft_compress_shortens_emdash():
    long_uk = (
        "У родині Лу вісім поколінь — один спадкоємець, рід ледь тримається, "
        "а тепер ти вагітна — у Лу нарешті є спадкоємець."
    )
    out = soft_compress_for_slot(long_uk, slot_ms=4500, target_lang="uk")
    assert " — " not in out or len(out) <= len(long_uk)
    assert len(out) <= len(long_uk)
