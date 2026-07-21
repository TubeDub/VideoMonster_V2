"""Semantic V3 Phase 2 tests — P31–P50 Native Meaning Pipeline."""

from __future__ import annotations

import pytest


def _run_phase2(**kwargs):
    from engines.semantic_v3.phase2 import run_semantic_v3_phase2

    segs = kwargs.pop(
        "segs",
        [
            "An 18-year-old boy named George Jr. drove through his hometown.",
            "He was on his way home for dinner.",
        ],
    )
    timing = kwargs.pop(
        "timing",
        [{"start": 0, "end": 4960}, {"start": 5100, "end": 8200}],
    )
    return run_semantic_v3_phase2(
        segs,
        timing,
        translate=True,
        translate_fn=lambda t, s, tg: f"[{tg}]{t}",
        tgt_lang="uk",
        **kwargs,
    )


def test_phase2_no_bridge_meta():
    proj = _run_phase2()
    assert proj.meta.get("bridge") is False
    assert proj.meta.get("phase2") is True
    assert proj.phase == "P50"
    assert proj.unit_type == "speech_unit"
    assert proj.meta.get("whisper_owner") is False


def test_phase2_native_translate_only_sentences():
    from engines.semantic_v3.native_translate import assert_not_whisper_unit
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    proj = _run_phase2()
    assert all(s.translated_text.startswith("[uk]") for s in proj.sentences if s.text)
    assert all(s.semantic_locked for s in proj.sentences if s.text)

    with pytest.raises(ArchitectureViolation):
        assert_not_whisper_unit({"unit_type": "whisper_segment"})


def test_phase2_word_phoneme_viseme():
    proj = _run_phase2()
    report = proj.meta.get("word_alignment") or {}
    assert report.get("word_count", 0) >= 10
    assert report.get("with_phonemes", 0) > 0
    assert report.get("with_visemes", 0) > 0
    w = proj.sentences[0].words[0]
    assert w.lemma
    assert w.language
    assert w.sentence_uuid
    assert w.phonemes
    assert w.visemes


def test_phase2_duration_predictor_not_char_based():
    from engines.semantic_v3.duration_predictor import predict_speech_duration

    a = predict_speech_duration("Hello world", voice="default")
    b = predict_speech_duration("Hi", voice="default")
    assert a.expected_ms > b.expected_ms
    assert a.method.startswith("phoneme")
    assert a.confidence > 0


def test_phase2_adaptive_plan_before_tts():
    from engines.semantic_v3.adaptive_planning import assert_tts_planned
    from engines.pipeline_integrity.exceptions import ArchitectureViolation
    from engines.semantic_v3.types import SemanticSentence

    proj = _run_phase2()
    for s in proj.sentences:
        assert getattr(s, "adaptive_plan", None) is not None
        assert_tts_planned(s)

    bare = SemanticSentence(text="x", start_ms=0, end_ms=100)
    with pytest.raises(ArchitectureViolation):
        assert_tts_planned(bare)


def test_phase2_scheduler_audio_units_no_double_voice():
    from engines.semantic_v3.scheduler_v2 import assert_no_double_voice, schedule_audio_units
    from engines.semantic_v3.speech_units import SpeechUnit
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    proj = _run_phase2()
    timeline = proj.meta.get("timeline") or {}
    assert timeline.get("units")
    speech = proj.meta.get("speech_units") or []
    assert len(speech) == len(timeline["units"])

    # Overlap must hard-fail (P47)
    bad = [
        SpeechUnit(
            speech_uuid="a",
            sentence_uuid="s1",
            text="A",
            source_text="A",
            start_ms=0,
            end_ms=1000,
            expected_duration_ms=1000,
        ),
        SpeechUnit(
            speech_uuid="b",
            sentence_uuid="s2",
            text="B",
            source_text="B",
            start_ms=500,
            end_ms=1500,
            expected_duration_ms=1000,
        ),
    ]
    # schedule pushes cursor — force overlap via mutated timeline
    tl = schedule_audio_units(bad, min_gap_ms=0)
    # Manually craft overlap
    from engines.semantic_v3.speech_units import AudioUnit, Timeline

    overlap_tl = Timeline(
        units=[
            AudioUnit("u1", "a", 0, 1000, 1000),
            AudioUnit("u2", "b", 900, 1900, 1000),
        ]
    )
    with pytest.raises(ArchitectureViolation):
        assert_no_double_voice(overlap_tl)


def test_phase2_orchestrator_export_from_speech_units():
    from engines.semantic_v3.phase2 import phase2_to_orchestrator_arrays

    proj = _run_phase2()
    sources, timing, texts = phase2_to_orchestrator_arrays(proj)
    assert len(sources) == len(timing) == len(texts)
    assert all(isinstance(t, dict) and "start" in t and "end" in t for t in timing)
    assert all(x.startswith("[uk]") for x in texts if x)


def test_phase2_context_memory_not_isolated():
    proj = _run_phase2()
    if len(proj.sentences) >= 2:
        assert proj.sentences[0].context_links
        assert any(str(r).startswith("ctx:") for r in proj.sentences[0].relations)


def test_phase2_decision_order_frozen():
    from engines.semantic_v3.adaptive_planning import DECISION_ORDER

    assert DECISION_ORDER[0] == "trim_silence"
    assert DECISION_ORDER[-1] == "manual_review"
    assert "semantic_rewrite" in DECISION_ORDER
    assert DECISION_ORDER.index("sentence_merge") < DECISION_ORDER.index("semantic_rewrite")


def test_phase2_quality_planner():
    proj = _run_phase2()
    qplans = proj.meta.get("quality_plans") or []
    assert qplans
    keys = {
        "meaning_score",
        "naturalness_score",
        "speech_score",
        "lipsync_score",
        "duration_score",
        "entity_score",
        "context_score",
        "prosody_score",
    }
    assert keys.issubset(qplans[0].keys())


def test_phase2_max_merge_configurable(monkeypatch):
    from engines.semantic_v3.adaptive_planning import max_merge_config

    monkeypatch.setenv("VM_SEMANTIC_MAX_MERGE", "7")
    assert max_merge_config() == 7


def test_phase2_rewrite_v2_preserves_numbers():
    from engines.semantic_v3.adaptive_planning import try_rewrite_v2
    from engines.semantic_v3.semantic_lock import lock_sentence
    from engines.semantic_v3.types import SemanticSentence

    s = SemanticSentence(
        text="I am going to buy 18 apples.",
        translated_text="I am going to buy 18 apples.",
        entities=["apples"],
        locked_entities=["apples"],
    )
    lock_sentence(s)
    out = try_rewrite_v2(s)
    assert "18" in (out.translated_text or "")
