"""Master Spec Part 3 — Translation Core tests (P201–P220)."""

from __future__ import annotations

import pytest


def test_backend_interface_and_registry():
    from engines.translation_core import get_backend, list_backends, translation_core_info
    from engines.translation_core.backend import TranslationBackend

    backends = list_backends()
    assert "identity" in backends
    assert "heuristic" in backends
    assert "mt_bridge" in backends
    eng = get_backend("identity")
    assert isinstance(eng, TranslationBackend)
    assert eng.health_check() is True
    assert eng.capabilities().offline is True
    assert eng.translate("Hello", src_lang="en", tgt_lang="uk") == "Hello"
    info = translation_core_info()
    assert "no_scheduler" in info["invariants"]


def test_sentence_only_forbidden_units():
    from engines.pipeline_integrity.exceptions import ArchitectureViolation
    from engines.translation_core import assert_sentence_only
    from engines.semantic_v3.types import SemanticSentence

    assert_sentence_only(SemanticSentence(text="Hi."))
    for ut in ("whisper_segment", "chunk", "window", "buffer", "audio_slot"):
        with pytest.raises(ArchitectureViolation):
            assert_sentence_only({"unit_type": ut})


def test_multipass_evaluator_and_lock():
    from engines.semantic_v3.types import SemanticSentence
    from engines.translation_core import translate_sentences

    s1 = SemanticSentence(
        text="Hello George, how are you today?",
        entities=["George"],
        emotion="calm",
        style="Interview",
    )
    s2 = SemanticSentence(
        text="I am fine.",
        emotion="joy",
        style="Interview",
    )
    result = translate_sentences(
        [s1, s2],
        src_lang="en",
        tgt_lang="uk",
        backend_id="heuristic",
        lock=True,
    )
    assert result.locked is True
    assert result.backend_id == "heuristic"
    assert len(result.reports) == 2
    rep = result.reports[0]
    assert len(rep.variants) >= 2
    assert rep.selected_text
    assert rep.selection_reason
    assert s1.semantic_locked is True
    assert s1.lock_status == "locked"
    assert "George" in s1.translated_text or "george" in s1.translated_text.lower()
    assert "translation_report" in (s1.context or {})


def test_entity_preservation_blocks_name_loss():
    from engines.pipeline_integrity.exceptions import ArchitectureViolation
    from engines.translation_core.terminology import assert_entities_preserved

    with pytest.raises(ArchitectureViolation):
        assert_entities_preserved(
            "George bought 18 apples",
            "He bought apples",
            entities=["George"],
        )


def test_hallucination_detector():
    from engines.translation_core.validators import hallucination_score, is_hallucination

    score, warns = hallucination_score("He arrived in 2010.", "He arrived in 2010 and also in 1999.")
    assert warns
    assert is_hallucination("short", "short " + "extra word " * 40 + "1999 2001 2003")


def test_rewrite_forbidden_after_lock():
    from engines.pipeline_integrity.exceptions import ArchitectureViolation
    from engines.translation_core.rewrite import safe_rewrite

    with pytest.raises(ArchitectureViolation):
        safe_rewrite("I will go", "I will go", locked=True)


def test_terminology_manager():
    from engines.translation_core.terminology import TerminologyManager

    tm = TerminologyManager(project_terms={"API": "API", "dubbing": "дубляж"})
    out = tm.apply("We love dubbing")
    assert "дубляж" in out


def test_architecture_isolation():
    from engines.translation_core.invariants import assert_translation_core_isolated

    assert_translation_core_isolated()


def test_phase2_uses_translation_core(monkeypatch):
    monkeypatch.setenv("VM_TRANSLATION_BACKEND", "heuristic")
    from engines.semantic_v3.phase2 import run_semantic_v3_phase2

    proj = run_semantic_v3_phase2(
        ["Hello George. How are you?"],
        [{"start": 0, "end": 3000}],
        src_lang="en",
        tgt_lang="uk",
        translate=True,
        translate_fn=None,
    )
    assert proj.sentences
    assert any(s.translated_text for s in proj.sentences)
    assert any(s.semantic_locked for s in proj.sentences)
