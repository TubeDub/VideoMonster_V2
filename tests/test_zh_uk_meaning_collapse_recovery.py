# -*- coding: utf-8 -*-
"""Regression: _tmp_3333 zh→uk meaning_collapse / dirty_mt_noop / integrity brick."""

from __future__ import annotations

# Exact shapes from diagnostic dump task 1258b2b68dcd4f2ca96b704124f260d5
ZH = (
    "我们入家八代单纯 此次担保 如今啊 你怀孕了 入家有后了 要是能一己得难 "
    "那就更完美了 那就更完美了 妈 只要是我一身的 无论是人是你 我都喜欢 "
    "请怀孕哥哥 我怀孕了 一个月之前那场意外 一个月前 我和娶妻妃同时被绑架 "
    "那晚她迎合绑匪 所以 这个孩子是绑匪 一个月之前的意外 这孩子是绑匪的 "
    "这孩子是绑匪的"
)
RAW_LOOP = (
    "Тепер ви вагітні, ви в будинку, ви в будинку, ви в будинку, "
    "ви в будинку, ви в будинку, ви в будинку, ви в будинку."
)
APPROVED_MIXED = (
    "Ми в родині осма покоління працювали над простотою. Це раз гаряча гарячка: "
    "тепер ти вагітна/вагіність. В нашій родині зараз буде наслідок. "
    "Якщо б це було лише моєю роботою, то було б ще краще. Ще краще. "
    "Мама, я люблю тебе, незалежно від того, хто мати. "
    "Будь ласка, дай мені зберегти цей ребенок. Я вагітна. "
    "Випадок місяця тому: місяць тому я та жінка були викрадені. "
    "Той晚上她迎合绑匪，所以这个孩子是绑匪的。"
)


def test_argos_loop_is_phrase_loop_and_collapse():
    from engines.mt.cross_script_guard import has_phrase_loop, meaning_collapse
    from engines.mt.dirty_mt import compute_dirty_mt_score

    assert has_phrase_loop(RAW_LOOP)
    hit = meaning_collapse(ZH, RAW_LOOP, source_lang="zh", target_lang="uk")
    assert hit is not None
    dirty = compute_dirty_mt_score(ZH, RAW_LOOP, tgt_lang="uk")
    assert dirty.dirty
    assert "phrase_loop" in dirty.reasons or "meaning_collapse" in dirty.reasons


def test_residual_cjk_in_approved_is_leak():
    from engines.mt.cross_script_guard import source_script_leak
    from engines.pipeline_language_gate import is_critical_language_mismatch

    leak = source_script_leak(ZH, APPROVED_MIXED, source_lang="zh", target_lang="uk")
    assert leak is not None
    assert leak.get("reason") == "residual_source_script"
    bad, code = is_critical_language_mismatch(
        APPROVED_MIXED, target_lang="uk", original=ZH, source_lang="zh"
    )
    assert bad
    assert "cjk" in code or "leak" in code


def test_strip_residual_cjk_yields_voiceable_uk():
    from engines.mt.cross_script_guard import (
        meaning_collapse,
        source_script_leak,
        strip_source_script_chars,
    )
    from engines.pipeline_language_gate import is_critical_language_mismatch

    scrubbed = strip_source_script_chars(
        APPROVED_MIXED, source_lang="zh", source=ZH
    )
    assert scrubbed
    assert not any("\u4e00" <= c <= "\u9fff" for c in scrubbed)
    assert source_script_leak(ZH, scrubbed, source_lang="zh", target_lang="uk") is None
    assert meaning_collapse(ZH, scrubbed, source_lang="zh", target_lang="uk") is None
    bad, _ = is_critical_language_mismatch(
        scrubbed, target_lang="uk", original=ZH, source_lang="zh"
    )
    assert not bad


def test_integrity_scrubs_instead_of_raw_mt_fallback():
    from engines.sentence_integrity import enforce_tts_integrity

    decision = enforce_tts_integrity(
        APPROVED_MIXED,
        fallbacks=[RAW_LOOP],
        source=ZH,
        tgt_lang="uk",
        source_lang="zh",
    )
    assert decision["chosen"] in ("scrubbed", "candidate")
    assert "ви в будинку" not in decision["text"]
    assert not any("\u4e00" <= c <= "\u9fff" for c in decision["text"])
    # Must NOT pick the Argos loop fallback
    assert decision["chosen"] != "fallback[0]"
    assert "ви в будинку, ви в будинку" not in decision["text"]
    rejected = decision.get("rejected") or []
    if rejected:
        rejected_codes = {
            c for r in rejected for c in (r.get("issues") or [])
        }
        assert "phrase_loop" in rejected_codes or "meaning_collapse" in rejected_codes


def test_salvage_prefers_scrubbed_approved():
    from engines.pipeline_language_gate import salvage_collapsed_segment_text

    text, method = salvage_collapsed_segment_text(
        text=RAW_LOOP,
        original=ZH,
        approved=APPROVED_MIXED,
        target_lang="uk",
        source_lang="zh",
    )
    assert text
    assert "scrub" in method or method.endswith("_ok")
    assert "ви в будинку, ви в будинку" not in text
    assert not any("\u4e00" <= c <= "\u9fff" for c in text)


def test_split_overlong_cjk_single_blob():
    from engines.segment_merger import split_overlong_cjk_segments

    texts, timing = split_overlong_cjk_segments(
        [ZH],
        [{"start": 1200, "end": 9200}],
        video_duration_ms=68300,
    )
    assert len(texts) >= 3
    assert len(texts) == len(timing)
    assert timing[0]["start"] < timing[-1]["end"]
    # Sparse island stretched toward full media
    assert timing[-1]["end"] - timing[0]["start"] > 20000
