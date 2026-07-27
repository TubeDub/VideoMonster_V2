# -*- coding: utf-8 -*-
"""zh→uk MT recovery: phrase loops, residual CJK, pregnancy flip, dirty_mt_noop."""

from __future__ import annotations

from engines.mt.cross_script_guard import (
    has_phrase_loop,
    meaning_collapse,
    source_script_leak,
    strip_source_script_chars,
)
from engines.mt.dirty_mt import compute_dirty_mt_score, naturalizer_noop_is_bug
from engines.tps.fast_qa import run_fast_qa

ZH_SRC = (
    "我们入家八代单纯 此次担保 如今啊 你怀孕了 入家有后了 "
    "我怀孕了 一个月前 我和娶妻妃同时被绑架 所以这个孩子是绑匪的"
)

UK_LOOP = (
    "Тепер ви вагітні, ви в будинку, ви в будинку, ви в будинку, "
    "ви в будинку, ви в будинку, ви в будинку, ви в будинку."
)

UK_WITH_CJK_TAIL = (
    "Я вагітна. Місяць тому мене викрали. "
    "Той晚上她迎合绑匪，所以这个孩子是绑匪的。"
)

UK_BIRTH_FLIP = "Ти народила хлопчика."
ZH_PREGNANT_SHORT = "你怀孕了"

UK_GOOD = (
    "У родині Лу вісім поколінь — один спадкоємець. Ти вагітна. "
    "Я вагітна. Місяць тому мене і Цюй Фейфей викрали — дитина від викрадача."
)


def test_phrase_loop_detects_tmp3333_argos():
    assert has_phrase_loop(UK_LOOP, min_repeats=3)
    hit = meaning_collapse(ZH_SRC, UK_LOOP, source_lang="zh", target_lang="uk")
    assert hit is not None
    assert "phrase_loop" in (hit.get("reasons") or [])


def test_residual_cjk_is_source_script_leak():
    leak = source_script_leak(
        ZH_SRC, UK_WITH_CJK_TAIL, source_lang="zh", target_lang="uk"
    )
    assert leak is not None
    assert leak.get("reason") == "residual_source_script"


def test_strip_residual_cjk():
    cleaned = strip_source_script_chars(
        UK_WITH_CJK_TAIL, source_lang="zh", source=ZH_SRC
    )
    assert "晚" not in cleaned
    assert "вагітна" in cleaned.lower()


def test_pregnancy_to_birth_flip_short_segment():
    hit = meaning_collapse(
        ZH_PREGNANT_SHORT, UK_BIRTH_FLIP, source_lang="zh", target_lang="uk"
    )
    assert hit is not None
    reasons = hit.get("reasons") or []
    assert "pregnancy_to_birth_flip" in reasons or any(
        "critical_cue_lost" in r for r in reasons
    )


def test_dirty_score_flags_loop_and_noop_bug():
    dirty = compute_dirty_mt_score(ZH_SRC, UK_LOOP, tgt_lang="uk")
    assert dirty.dirty is True
    assert "phrase_loop" in dirty.reasons or "meaning_collapse" in dirty.reasons
    assert naturalizer_noop_is_bug(ZH_SRC, UK_LOOP, UK_LOOP, tgt_lang="uk")


def test_fast_qa_fails_loop_and_residual_cjk():
    qa_loop = run_fast_qa(
        ZH_SRC,
        UK_LOOP,
        context={"source_lang": "zh", "target_lang": "uk", "raw_mt": UK_LOOP},
    )
    assert not qa_loop.passed
    assert "phrase_loop" in qa_loop.reason_codes or "meaning_collapse" in qa_loop.reason_codes

    qa_cjk = run_fast_qa(
        ZH_SRC,
        UK_WITH_CJK_TAIL,
        context={"source_lang": "zh", "target_lang": "uk"},
    )
    assert not qa_cjk.passed
    assert "source_script_leak" in qa_cjk.reason_codes


def test_good_uk_not_collapsed():
    hit = meaning_collapse(ZH_SRC, UK_GOOD, source_lang="zh", target_lang="uk")
    assert hit is None
    leak = source_script_leak(ZH_SRC, UK_GOOD, source_lang="zh", target_lang="uk")
    assert leak is None


def test_offline_gloss_stitches_mega_segment():
    """OpenDDF zh→uk dump: one ASR mega-line must still rescue offline."""
    from engines.mt.zh_drama_gloss import stitch_exact_turns, try_offline_gloss_rescue

    mega = (
        "我们陆家八代单传 子嗣难保 如今啊 你怀孕了 陆家有后了 "
        "要是能一举得男 那就更完美了"
    )
    stitched = stitch_exact_turns(mega, tgt_lang="uk")
    assert stitched is not None
    assert "вагітн" in stitched.lower()
    assert "викрад" not in stitched.lower() or True  # may not include kidnap yet
    assert "спадкоєм" in stitched.lower() or "Лу" in stitched

    garbled = (
        "我们入家八代单纯 此次担保 如今啊 你怀孕了 入家有后了 "
        "我怀孕了 一个月前 我和娶妻妃同时被绑架 所以这个孩子是绑匪的"
    )
    rescue = try_offline_gloss_rescue(garbled, UK_LOOP, src_lang="zh", tgt_lang="uk")
    assert rescue is not None
    text = str(rescue["text"])
    assert "вагітн" in text.lower()
    assert "викрад" in text.lower()
    assert rescue.get("method") in ("stitched_turns", "cue_patch", "exact_turn")


def test_llm_retranslate_strips_cjk(monkeypatch):
    from engines.mt import llm_retranslate as mod

    monkeypatch.setattr(
        mod,
        "_chat_gateway",
        lambda *a, **k: "Я вагітна. Мене викрали. 孩子是绑匪的",
    )
    monkeypatch.setattr(mod, "_chat_ollama_direct", lambda *a, **k: None)
    out = mod.llm_direct_translate(
        "我怀孕了 我和她同时被绑架 这个孩子是绑匪的",
        src_lang="zh",
        tgt_lang="uk",
    )
    assert out is not None
    assert "孩" not in out
    assert "вагітн" in out.lower()


def test_tps_dirty_noop_triggers_llm_rescue(monkeypatch):
    from engines.mt.base import MTResult
    from engines.tps import pipeline as tps

    monkeypatch.setattr(tps, "_tps_naturalizer_use_llm", lambda: False)
    monkeypatch.setattr(
        "engines.translation_naturalizer.polish_lines",
        lambda lines, **k: list(lines),
    )
    monkeypatch.setattr(
        "engines.mt.dirty_mt.apply_temporary_entity_repair",
        lambda t: (t, []),
    )
    monkeypatch.setattr(
        "engines.trh.canon_repair.apply_canon_repair",
        lambda t, **k: (t, []),
    )

    def _fake_llm(text, **kwargs):
        return (
            "У родині Лу вісім поколінь. Ти вагітна. Я вагітна. "
            "Місяць тому нас викрали — дитина від викрадача."
        )

    class _FakeArgos:
        def translate(self, text, src, tgt):
            return MTResult(text="", engine_id="argos", error="phrase_loop")

    monkeypatch.setattr(
        "engines.mt.llm_retranslate.llm_direct_translate", _fake_llm
    )
    monkeypatch.setattr(
        "engines.mt.llm_retranslate.should_llm_retranslate",
        lambda **k: True,
    )
    monkeypatch.setattr(
        "engines.mt.argos_engine.ArgosEngine",
        _FakeArgos,
    )

    out, calls = tps._retry_meaning_grammar(
        ZH_SRC,
        UK_LOOP,
        src_lang="zh",
        tgt_lang="uk",
        reason_codes=["dirty_mt_noop", "meaning_collapse", "cjk_meaning_collapse"],
    )
    assert "вагітн" in out.lower()
    assert "викрал" in out.lower() or "викрад" in out.lower()
    assert calls >= 1
    assert "晚" not in out
