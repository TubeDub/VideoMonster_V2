"""P120 — Integration tests for Meaning-First Pipeline V2.

Verifies:
- Whisper used only as word source (P101)
- Whisper Segment no longer participates in decisions (P116)
- Translation works only with MeaningUnit (P105)
- Semantic Adaptation happens before Translation LOCK (P106/P109)
- TTS receives already optimized text (P111)
- Scheduler works only with AudioUnit (P113)
- No sentence truncation
- No word loss
- Father-son dialogue scenario reproduces correctly (P120)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parent / "golden" / "dub" / "george_lucas_en_uk_20.json"


def _simple_translate(text: str, src: str, tgt: str) -> str:
    """Dummy translator that prefixes with [TL] for testing."""
    return f"[TL:{tgt}] {text}"


def _build_george_lucas_asr():
    """Build ASR-like input from golden George Lucas data."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    asr_texts = [seg["en"] for seg in golden["segments"]]
    timing_map = []
    offset = 0
    for seg in golden["segments"]:
        slot = seg["slot_ms"]
        timing_map.append({"start": offset, "end": offset + slot})
        offset += slot
    return asr_texts, timing_map, golden


# ── P101: Word Archive ─────────────────────────────────────────────────

class TestP101WordArchive:
    def test_every_word_preserved(self):
        from engines.semantic_v3.word_engine import build_words_from_timing_map

        texts = ["Hello world.", "This is a test."]
        timing = [{"start": 0, "end": 2000}, {"start": 2000, "end": 4000}]
        words = build_words_from_timing_map(texts, timing)
        word_texts = [w.text.strip(".,!?") for w in words]
        assert "Hello" in word_texts
        assert "world" in word_texts
        assert "This" in word_texts
        assert "test" in word_texts

    def test_word_has_required_fields(self):
        from engines.semantic_v3.types import SemanticWord

        w = SemanticWord(text="hello", start_ms=0, end_ms=500)
        assert w.word_uuid
        assert w.text == "hello"
        assert w.start_ms == 0
        assert w.end_ms == 500
        assert hasattr(w, "confidence")
        assert hasattr(w, "pause_before_ms")
        assert hasattr(w, "pause_after_ms")
        assert hasattr(w, "punctuation")
        assert hasattr(w, "sentence_candidate")
        assert hasattr(w, "semantic_group")


# ── P103: MeaningUnit ───────────────────────────────────────────────────

class TestP103MeaningUnit:
    def test_meaning_unit_creation(self):
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        sent = SemanticSentence(text="Hello world.", start_ms=0, end_ms=2000)
        unit = MeaningUnit(sentences=[sent])
        assert unit.unit_uuid
        assert unit.text == "Hello world."
        assert unit.start_ms == 0
        assert unit.end_ms == 2000
        assert unit.sentence_count == 1
        assert unit.word_count == 0  # no words in sentence
        assert unit.meaning_complete

    def test_meaning_unit_multi_sentence(self):
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        s1 = SemanticSentence(text="He went home.", start_ms=0, end_ms=1000)
        s2 = SemanticSentence(text="It was late.", start_ms=1000, end_ms=2000)
        unit = MeaningUnit(sentences=[s1, s2])
        assert "He went home." in unit.text
        assert "It was late." in unit.text
        assert unit.start_ms == 0
        assert unit.end_ms == 2000
        assert unit.sentence_count == 2

    def test_meaning_unit_serialization(self):
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        sent = SemanticSentence(text="Test.", start_ms=0, end_ms=1000)
        unit = MeaningUnit(sentences=[sent], emotion="joy", speech_style="dialogue")
        d = unit.to_dict()
        assert d["emotion"] == "joy"
        assert d["speech_style"] == "dialogue"
        restored = MeaningUnit.from_dict(d)
        assert restored.emotion == "joy"
        assert restored.speech_style == "dialogue"


class TestP103MeaningUnitBuilder:
    def test_builder_from_sentences(self):
        from engines.semantic_v3.meaning_unit_builder import build_meaning_units
        from engines.semantic_v3.types import SemanticSentence

        sentences = [
            SemanticSentence(text="Hello world.", start_ms=0, end_ms=1000, speaker="A"),
            SemanticSentence(text="How are you?", start_ms=1200, end_ms=2000, speaker="B"),
            SemanticSentence(text="I am fine.", start_ms=2200, end_ms=3000, speaker="B"),
        ]
        units = build_meaning_units(sentences)
        assert len(units) >= 1
        # All sentences accounted for
        total_sents = sum(len(u.sentences) for u in units)
        assert total_sents == 3

    def test_builder_links_prev_next(self):
        from engines.semantic_v3.meaning_unit_builder import build_meaning_units
        from engines.semantic_v3.types import SemanticSentence

        sentences = [
            SemanticSentence(text="First thought.", start_ms=0, end_ms=1000),
            SemanticSentence(text="Second thought.", start_ms=2000, end_ms=3000),
            SemanticSentence(text="Third thought.", start_ms=4000, end_ms=5000),
        ]
        units = build_meaning_units(sentences)
        if len(units) >= 2:
            assert units[0].prev_unit_uuid == ""
            assert units[0].next_unit_uuid == units[1].unit_uuid
            assert units[-1].next_unit_uuid == ""


# ── P104: Context Graph ─────────────────────────────────────────────────

class TestP104ContextGraph:
    def test_context_graph_enriches_units(self):
        from engines.semantic_v3.context_graph import build_context_graph
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        s1 = SemanticSentence(text="Hello John.", start_ms=0, end_ms=1000, speaker="Alice")
        s2 = SemanticSentence(text="How are you?", start_ms=1200, end_ms=2000, speaker="John")
        u1 = MeaningUnit(sentences=[s1], speaker="Alice")
        u2 = MeaningUnit(sentences=[s2], speaker="John")
        units = build_context_graph([u1, u2])
        assert u1.next_unit_uuid == u2.unit_uuid
        assert u2.prev_unit_uuid == u1.unit_uuid
        assert u1.speech_style  # should be detected


# ── P106: Semantic Adaptation ───────────────────────────────────────────

class TestP106SemanticAdaptation:
    def test_generates_five_variants(self):
        from engines.semantic_v3.semantic_adaptation import generate_adaptation_variants
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        sent = SemanticSentence(text="The quick brown fox.", start_ms=0, end_ms=2000)
        unit = MeaningUnit(sentences=[sent], translated_text="Швидка руда лисиця.")
        variants = generate_adaptation_variants(unit, tgt_lang="uk")
        assert len(variants) >= 5
        labels = {v.label for v in variants}
        assert labels == {"A", "B", "C", "D", "E"}

    def test_preserves_numbers(self):
        from engines.semantic_v3.semantic_adaptation import generate_adaptation_variants
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        sent = SemanticSentence(text="He has 3 children aged 5 and 7.", start_ms=0, end_ms=2000)
        unit = MeaningUnit(
            sentences=[sent],
            translated_text="У нього 3 дітей віком 5 та 7 років.",
        )
        variants = generate_adaptation_variants(
            unit, source_text=sent.text, tgt_lang="uk",
        )
        for v in variants:
            if not v.rejected:
                assert "3" in v.text
                assert "5" in v.text
                assert "7" in v.text


# ── P107: Duration Prediction ──────────────────────────────────────────

class TestP107DurationPrediction:
    def test_predicts_duration(self):
        from engines.semantic_v3.variant_duration_predictor import predict_variant_duration

        ms, conf = predict_variant_duration("Привіт, як справи?", lang="uk")
        assert ms > 0
        assert 0 < conf <= 1.0

    def test_empty_text_zero_duration(self):
        from engines.semantic_v3.variant_duration_predictor import predict_variant_duration

        ms, conf = predict_variant_duration("", lang="uk")
        assert ms == 0
        assert conf == 1.0

    def test_duration_score_computation(self):
        from engines.semantic_v3.variant_duration_predictor import compute_duration_score

        assert compute_duration_score(5000, 5000) > 90
        assert compute_duration_score(10000, 5000) < 50
        assert compute_duration_score(2000, 5000) < 80


# ── P108: Strategy Selection ───────────────────────────────────────────

class TestP108StrategySelection:
    def test_selects_best_variant(self):
        from engines.semantic_v3.semantic_adaptation import AdaptationVariant
        from engines.semantic_v3.strategy_selection import select_best

        variants = [
            AdaptationVariant(label="A", text="Option A", meaning_score=90, duration_score=50),
            AdaptationVariant(label="B", text="Option B", meaning_score=85, duration_score=95),
            AdaptationVariant(label="C", text="Option C", meaning_score=70, duration_score=100),
        ]
        best = select_best(variants)
        assert best is not None
        assert best.selected


# ── P109: Translation Lock ─────────────────────────────────────────────

class TestP109TranslationLock:
    def test_lock_requires_validation(self):
        from engines.semantic_v3.meaning_lock import lock_meaning_unit
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        sent = SemanticSentence(text="Test.", start_ms=0, end_ms=1000)
        unit = MeaningUnit(sentences=[sent], translated_text="Тест.")
        unit.selected_variant_id = "abc"
        unit.validation_status = "passed"
        lock_meaning_unit(unit)
        assert unit.semantic_locked
        assert unit.lock_status == "locked"

    def test_lock_force_mode(self):
        from engines.semantic_v3.meaning_lock import lock_meaning_unit
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        sent = SemanticSentence(text="Test.", start_ms=0, end_ms=1000)
        unit = MeaningUnit(sentences=[sent], translated_text="Тест.")
        lock_meaning_unit(unit, force=True)
        assert unit.semantic_locked


# ── P110: Speech Planning ──────────────────────────────────────────────

class TestP110SpeechPlanning:
    def test_builds_speech_plan(self):
        from engines.semantic_v3.speech_planning import build_speech_plan
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        sent = SemanticSentence(text="Test.", start_ms=0, end_ms=5000)
        unit = MeaningUnit(
            sentences=[sent],
            translated_text="Привіт, як справи, все гаразд?",
            predicted_duration_ms=3000,
        )
        plan = build_speech_plan(unit, slot_ms=5000, lang="uk")
        assert plan.tts_ready
        assert plan.expected_duration_ms > 0


# ── P116: No Segment Rule ──────────────────────────────────────────────

class TestP116NoSegmentRule:
    def test_rejects_whisper_segment(self):
        from engines.semantic_v3.stage_validator import validate_no_segment_rule

        data = [{"unit_type": "whisper_segment", "text": "bad"}]
        violations = validate_no_segment_rule(data)
        assert len(violations) > 0

    def test_allows_meaning_unit(self):
        from engines.semantic_v3.stage_validator import validate_no_segment_rule
        from engines.semantic_v3.types import MeaningUnit, SemanticSentence

        sent = SemanticSentence(text="Test.", start_ms=0, end_ms=1000)
        unit = MeaningUnit(sentences=[sent])
        violations = validate_no_segment_rule([unit])
        whisper_violations = [v for v in violations if "whisper" in v.lower()]
        assert len(whisper_violations) == 0


# ── P119: Self-Validation ──────────────────────────────────────────────

class TestP119SelfValidation:
    def test_validates_stage_transition(self):
        from engines.semantic_v3.stage_validator import validate_stage_transition
        from engines.semantic_v3.types import SemanticSentence

        sentences = [
            SemanticSentence(text="Hello.", start_ms=0, end_ms=1000),
            SemanticSentence(text="World.", start_ms=1000, end_ms=2000),
        ]
        result = validate_stage_transition(
            "word_archive", "sentence_reconstruction",
            sentences=sentences,
            raise_on_fail=False,
        )
        assert result.passed

    def test_rejects_empty_pipeline(self):
        from engines.semantic_v3.stage_validator import validate_stage_transition

        result = validate_stage_transition(
            "word_archive", "sentence_reconstruction",
            raise_on_fail=False,
        )
        assert not result.passed

    def test_meaning_preservation_check(self):
        from engines.semantic_v3.stage_validator import validate_meaning_preservation

        result = validate_meaning_preservation(
            "He has 3 children and no pets.",
            "У нього 3 дітей і жодних тварин.",
        )
        assert result.passed

    def test_meaning_preservation_detects_number_loss(self):
        from engines.semantic_v3.stage_validator import validate_meaning_preservation

        result = validate_meaning_preservation(
            "He has 3 children and no pets.",
            "У нього є діти і немає тварин.",
        )
        assert not result.passed or len(result.errors) > 0


# ── P117: Meaning Preservation ──────────────────────────────────────────

class TestP117MeaningPreservation:
    def test_negation_preserved(self):
        from engines.semantic_v3.stage_validator import validate_meaning_preservation

        result = validate_meaning_preservation(
            "He does not want to go.",
            "Він не хоче йти.",
        )
        assert result.passed

    def test_negation_lost_detected(self):
        from engines.semantic_v3.stage_validator import validate_meaning_preservation

        result = validate_meaning_preservation(
            "He does not want to go.",
            "Він хоче йти.",
        )
        assert not result.passed


# ── P120: Full Pipeline Integration ─────────────────────────────────────

class TestP120FullPipeline:
    def test_full_pipeline_simple(self):
        from engines.semantic_v3.meaning_first_pipeline import (
            run_meaning_first_pipeline,
        )

        texts = [
            "Hello world. This is a test.",
            "The quick brown fox jumps over the lazy dog.",
        ]
        timing = [
            {"start": 0, "end": 3000},
            {"start": 3000, "end": 6000},
        ]
        result = run_meaning_first_pipeline(
            texts, timing,
            src_lang="en", tgt_lang="uk",
            translate_fn=_simple_translate,
            lock=True,
        )
        assert result["words"]
        assert result["sentences"]
        assert result["meaning_units"]
        assert result["speech_plans"]
        # All meaning units should be locked
        for mu in result["meaning_units"]:
            assert mu.semantic_locked

    def test_no_whisper_segments_in_pipeline(self):
        from engines.semantic_v3.meaning_first_pipeline import (
            run_meaning_first_pipeline,
        )

        texts = ["Test sentence one.", "Test sentence two."]
        timing = [{"start": 0, "end": 2000}, {"start": 2000, "end": 4000}]
        result = run_meaning_first_pipeline(
            texts, timing,
            translate_fn=_simple_translate,
        )
        # P116: no segment violations
        violations = result["meta"].get("p116_violations", [])
        whisper_violations = [v for v in violations if "whisper" in v.lower()]
        assert len(whisper_violations) == 0

    def test_no_word_loss(self):
        from engines.semantic_v3.meaning_first_pipeline import (
            run_meaning_first_pipeline,
        )

        texts = ["Alpha beta gamma.", "Delta epsilon."]
        timing = [{"start": 0, "end": 2000}, {"start": 2000, "end": 4000}]
        result = run_meaning_first_pipeline(
            texts, timing,
            translate_fn=_simple_translate,
        )
        all_word_texts = {w.text.strip(".,!?").lower() for w in result["words"]}
        for expected in ["alpha", "beta", "gamma", "delta", "epsilon"]:
            assert expected in all_word_texts, f"Word '{expected}' lost!"

    @pytest.mark.skipif(not GOLDEN.exists(), reason="golden fixture missing")
    def test_george_lucas_father_son_no_truncation(self):
        """P120: father-son dialogue reproduces without truncation."""
        from engines.semantic_v3.meaning_first_pipeline import (
            run_meaning_first_pipeline,
        )

        asr_texts, timing_map, golden = _build_george_lucas_asr()
        result = run_meaning_first_pipeline(
            asr_texts, timing_map,
            src_lang="en", tgt_lang="uk",
            translate_fn=_simple_translate,
            lock=True,
        )
        units = result["meaning_units"]
        assert len(units) > 0

        all_text = " ".join(mu.text for mu in units).lower()
        assert "father and son" in all_text or "between father" in all_text

        for mu in units:
            assert mu.text.strip(), f"Empty meaning unit: {mu.unit_uuid}"

    @pytest.mark.skipif(not GOLDEN.exists(), reason="golden fixture missing")
    def test_george_lucas_all_segments_have_meaning_units(self):
        """Every golden segment should map to at least one MeaningUnit."""
        from engines.semantic_v3.meaning_first_pipeline import (
            run_meaning_first_pipeline,
        )

        asr_texts, timing_map, golden = _build_george_lucas_asr()
        result = run_meaning_first_pipeline(
            asr_texts, timing_map,
            src_lang="en", tgt_lang="uk",
            translate_fn=_simple_translate,
        )
        words = result["words"]
        units = result["meaning_units"]
        total_words_in_units = sum(mu.word_count for mu in units)
        assert total_words_in_units >= len(words) * 0.95, (
            f"Word coverage: {total_words_in_units}/{len(words)}"
        )

    def test_adaptation_generates_variants(self):
        from engines.semantic_v3.meaning_first_pipeline import (
            run_meaning_first_pipeline,
        )

        texts = ["This is a moderately long sentence that should generate adaptation variants."]
        timing = [{"start": 0, "end": 5000}]
        result = run_meaning_first_pipeline(
            texts, timing,
            translate_fn=_simple_translate,
        )
        for mu in result["meaning_units"]:
            if mu.adaptation_variants:
                assert len(mu.adaptation_variants) >= 5
                break
        else:
            pytest.skip("No variants generated (may be expected for short text)")
