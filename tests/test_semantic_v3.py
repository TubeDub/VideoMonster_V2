"""Semantic V3 unit/architecture tests — Meaning First spine."""

from __future__ import annotations

import os

import pytest


def test_words_and_sentences_from_whisper_like_input():
    from engines.semantic_v3.pipeline import run_semantic_v3_from_asr

    segs = [
        "An 18-year-old boy named George Jr. drove through his hometown.",
        "He was on his way home for dinner.",
    ]
    timing = [{"start": 0, "end": 4960}, {"start": 5100, "end": 8200}]
    proj = run_semantic_v3_from_asr(segs, timing, tgt_lang="uk", run_adaptation=True)
    assert proj.unit_type == "semantic_sentence"
    assert len(proj.asr_archive) == 2
    assert len(proj.words) >= 10
    assert len(proj.sentences) >= 1
    for s in proj.sentences:
        assert s.dub_segment_uuid
        assert s.start_ms <= s.end_ms


def test_sentence_not_split_absolute_rule():
    from engines.semantic_v3.absolute_rules import assert_no_sentence_split_across_segments
    from engines.semantic_v3.types import SemanticSentence

    s = SemanticSentence(text="Hello.", start_ms=0, end_ms=1000, dub_segment_uuid="x")
    assert_no_sentence_split_across_segments([s])


def test_semantic_lock_blocks_entity_loss():
    from engines.pipeline_integrity.exceptions import ArchitectureViolation
    from engines.semantic_v3.semantic_lock import (
        assert_semantic_rewrite_allowed,
        lock_sentence,
    )
    from engines.semantic_v3.types import SemanticSentence

    s = SemanticSentence(text="George drove home.", entities=["George"])
    lock_sentence(s)
    with pytest.raises(ArchitectureViolation):
        assert_semantic_rewrite_allowed(
            s,
            "He drove home.",
            meaning_similarity=0.95,
            entity_preservation=0.0,
        )


def test_semantic_rewrite_shortens_naturally():
    from engines.semantic_v3.adaptation import semantic_rewrite

    assert "don't" in semantic_rewrite("I did not go").lower() or "didn't" in semantic_rewrite(
        "I did not go"
    ).lower()
    out = semantic_rewrite("у той момент, коли він прийшов")
    assert "коли" in out.lower()


def test_pipeline_arrays_bridge():
    from engines.semantic_v3.pipeline import run_semantic_v3_from_asr

    segs = ["Hello world. This is fine."]
    timing = [{"start": 0, "end": 3000}]
    proj = run_semantic_v3_from_asr(segs, timing)
    src, tm, rows = proj.to_pipeline_arrays()
    assert len(src) == len(tm) == len(rows)
    assert all(r.get("sentence_uuid") for r in rows)


def test_flag_env(monkeypatch):
    from engines.semantic_v3 import semantic_v3_enabled

    monkeypatch.setenv("VM_SEMANTIC_V3", "1")
    assert semantic_v3_enabled() is True
    monkeypatch.setenv("VM_SEMANTIC_V3", "0")
    assert semantic_v3_enabled() is False


def test_no_overlap_detection():
    from engines.pipeline_integrity.exceptions import ArchitectureViolation
    from engines.semantic_v3.absolute_rules import assert_no_overlap_slots
    from engines.semantic_v3.types import SemanticSentence

    a = SemanticSentence(text="A.", start_ms=0, end_ms=1000)
    b = SemanticSentence(text="B.", start_ms=900, end_ms=2000)
    with pytest.raises(ArchitectureViolation):
        assert_no_overlap_slots([a, b])
