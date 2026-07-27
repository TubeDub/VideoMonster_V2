"""Regression: LLM retranslate recovers zh→uk meaning collapse."""

from __future__ import annotations

from engines.mt.llm_retranslate import (
    _collapse_acceptable,
    _cue_glossary,
    should_llm_retranslate,
)


def test_should_llm_retranslate_zh_uk():
    assert should_llm_retranslate(src_lang="zh", tgt_lang="uk")
    assert not should_llm_retranslate(src_lang="en", tgt_lang="uk")


def test_cue_glossary_includes_kidnap_and_pregnancy():
    src = "你怀孕了 我们被绑架 这个孩子是绑匪的 一场意外"
    gloss = _cue_glossary(src, tgt_lang="uk")
    joined = " | ".join(gloss)
    assert "вагіт" in joined
    assert "викрад" in joined
    assert "дитина" in joined
    assert "випадок" in joined


def test_collapse_acceptable_half_cues():
    assert _collapse_acceptable(
        {
            "missing_gloss": ["绑架"],
            "source_hits": ["怀孕", "绑架", "孩子", "意外"],
        }
    )
    assert not _collapse_acceptable(
        {
            "missing_gloss": ["怀孕", "绑架", "孩子"],
            "source_hits": ["怀孕", "绑架", "孩子", "意外"],
        }
    )
