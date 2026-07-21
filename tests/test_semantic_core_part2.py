"""Master Spec Part 2 — Semantic Core tests (P101–P120)."""

from __future__ import annotations

import pytest


def _words_hello_john():
    from engines.semantic_v3.types import SemanticWord

    # Whisper-like fragmentation
    return [
        SemanticWord(text="Hello", start_ms=0, end_ms=400),
        SemanticWord(text="John", start_ms=450, end_ms=800, pause_before_ms=50),
        SemanticWord(text="How", start_ms=1200, end_ms=1400, pause_before_ms=400),
        SemanticWord(text="are", start_ms=1450, end_ms=1600),
        SemanticWord(text="you", start_ms=1650, end_ms=1900),
    ]


def test_word_model_p101_fields():
    from engines.semantic_v3.word_model import enrich_word_model

    words = enrich_word_model(_words_hello_john(), language="en", scene_uuid="sc1")
    w = words[0]
    assert w.normalized_text
    assert w.lemma
    assert w.language == "en"
    assert w.scene_uuid == "sc1"
    assert w.prosody
    assert w.pause_before == w.pause_before_ms


def test_boundary_optimizer_repairs_whisper_fragments():
    from engines.semantic_v3.boundary_optimizer import optimize_boundaries

    sentences = optimize_boundaries(_words_hello_john())
    texts = [s.text for s in sentences]
    joined = " ".join(texts).lower()
    assert "hello" in joined and "john" in joined
    # Should produce a question for How are you
    assert any("?" in s.text or s.is_question for s in sentences)
    # Greeting+name should be joined or marked address
    assert any(s.has_address or "john" in s.text.lower() for s in sentences)


def test_word_graph_and_entity_graph():
    from engines.semantic_v3.boundary_optimizer import optimize_boundaries
    from engines.semantic_v3.entity_graph import build_entity_graph
    from engines.semantic_v3.semantic_graph import analyze_all
    from engines.semantic_v3.types import SemanticWord
    from engines.semantic_v3.word_graph import build_word_graph
    from engines.semantic_v3.word_model import enrich_word_model

    words = enrich_word_model(
        [
            SemanticWord(text="George", start_ms=0, end_ms=300, entity="PERSON"),
            SemanticWord(text="drove", start_ms=320, end_ms=600),
            SemanticWord(text="home", start_ms=620, end_ms=900),
            SemanticWord(text=".", start_ms=900, end_ms=920),
            SemanticWord(text="He", start_ms=1100, end_ms=1200),
            SemanticWord(text="was", start_ms=1220, end_ms=1400),
            SemanticWord(text="tired", start_ms=1420, end_ms=1700),
            SemanticWord(text=".", start_ms=1700, end_ms=1720),
        ]
    )
    # Force end punct on last content tokens for builder
    words[2].text = "home."
    words[6].text = "tired."
    sentences = analyze_all(optimize_boundaries(words))
    wg = build_word_graph(sentences)
    assert wg.edges
    eg = build_entity_graph(sentences)
    assert eg.nodes
    # Same entity id for George mentions if present
    george_nodes = [n for n in eg.nodes.values() if "george" in n.canonical.lower()]
    assert george_nodes


def test_semantic_core_pipeline_full():
    from engines.semantic_v3.semantic_core import (
        assert_semantic_sentence_only,
        clear_project_memory,
        run_semantic_core,
    )
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    segs = [
        "An 18-year-old boy named George Jr. drove through his hometown.",
        "He was on his way home for dinner.",
        "How are you today?",
    ]
    timing = [
        {"start": 0, "end": 5000},
        {"start": 5200, "end": 8000},
        {"start": 8500, "end": 10000},
    ]
    proj = run_semantic_core(
        segs, timing, src_lang="en", content_mode="interview", project_uuid="testproj"
    )
    assert proj.phase == "P120"
    assert proj.meta.get("whisper_owner") is False
    assert proj.meta.get("semantic_core") is True
    assert proj.meta.get("style")
    assert proj.meta.get("word_graph", {}).get("count", 0) >= 0
    assert proj.meta.get("entity_graph", {}).get("nodes") is not None
    assert proj.meta.get("conversation_memory")
    assert proj.meta.get("lock_preparation")
    assert all(s.lock_status == "prepared" for s in proj.sentences)
    assert all(s.sentence_confidence > 0 for s in proj.sentences)
    assert all(s.style for s in proj.sentences)
    assert all(s.emotion for s in proj.sentences)

    for s in proj.sentences:
        assert_semantic_sentence_only(s)

    with pytest.raises(ArchitectureViolation):
        assert_semantic_sentence_only({"unit_type": "whisper_segment"})

    mem = clear_project_memory(proj)
    assert mem is not None and mem.cleared is True


def test_emotion_and_style_engines():
    from engines.semantic_v3.emotion_engine import detect_emotion
    from engines.semantic_v3.style_engine import SUPPORTED_STYLES, detect_style
    from engines.semantic_v3.types import SemanticSentence

    s = SemanticSentence(text="I am so happy today!")
    assert detect_emotion(s) == "joy"
    style = detect_style([s], content_mode="youtube")
    assert style == "YouTube"
    assert "Movie" in SUPPORTED_STYLES


def test_dialogue_and_scene_context():
    from engines.semantic_v3.dialogue_engine import build_dialogues
    from engines.semantic_v3.scene_context import assign_scenes
    from engines.semantic_v3.types import SemanticSentence

    a = SemanticSentence(
        text="How are you?",
        start_ms=0,
        end_ms=1000,
        speaker="A",
        is_question=True,
        is_dialogue=True,
    )
    b = SemanticSentence(
        text="I am fine.",
        start_ms=1100,
        end_ms=2000,
        speaker="B",
        is_dialogue=True,
    )
    c = SemanticSentence(
        text="Later that night the city slept.",
        start_ms=12000,
        end_ms=15000,
        speaker="NARR",
        entities=["city"],
    )
    dlgs = build_dialogues([a, b, c])
    assert dlgs
    assert a.dialogue_id
    scenes = assign_scenes([a, b, c], gap_ms=5000)
    assert len(scenes) >= 2
    assert a.scene_uuid != c.scene_uuid


def test_semantic_validator_flags_incomplete():
    from engines.semantic_v3.semantic_validator import validate_semantic_sentences
    from engines.semantic_v3.types import SemanticSentence

    bad = SemanticSentence(
        text="and then",
        words=[],
        is_incomplete=True,
        sentence_type="incomplete",
        sentence_confidence=0.4,
    )
    report = validate_semantic_sentences([bad], min_confidence=0.7)
    assert report.ok is False
    assert bad.sentence_uuid in report.reanalyze
    assert bad.semantic_status == "needs_review"


def test_forbidden_units_p117():
    from engines.semantic_v3.semantic_core import assert_semantic_sentence_only
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    for ut in ("chunk", "buffer", "window", "whisper_segment"):
        with pytest.raises(ArchitectureViolation):
            assert_semantic_sentence_only({"unit_type": ut})
