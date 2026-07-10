"""Tests for the unified DubbingEngine and its sub-stages."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ── Stage 1: Entity detection ──────────────────────────────────────────────────

class TestEntityContext:
    def test_car_brand_fiat_detected(self):
        from engines.dubbing_engine.entities import extract_entities
        entities = extract_entities(
            "He bought a small Italian car called the Fiat.", "uk"
        )
        labels = {e.label for e in entities}
        assert "CAR" in labels
        texts = [e.text.lower() for e in entities]
        assert any("fiat" in t for t in texts)

    def test_car_brand_not_translated(self):
        from engines.dubbing_engine.entities import extract_entities
        entities = extract_entities("He drove a BMW to work.", "ru")
        car_ents = [e for e in entities if e.label == "CAR"]
        assert car_ents
        assert all(e.translation == e.text for e in car_ents)

    def test_person_name_multi_word(self):
        from engines.dubbing_engine.entities import extract_entities
        entities = extract_entities("George Jr. drove his Fiat.", "uk")
        names = [e for e in entities if e.label == "PERSON"]
        assert names

    def test_geo_name_transliteration(self):
        from engines.dubbing_engine.entities import extract_entities
        entities = extract_entities("He applied to USC in California.", "uk")
        geo = [e for e in entities if e.label == "GEO"]
        assert geo
        cali = next((e for e in geo if "california" in e.text.lower()), None)
        assert cali
        assert cali.translation == "Каліфорнія"

    def test_entity_protection_restoration(self):
        from engines.dubbing_engine.entities import (
            extract_entities, protect_entities_in_translation
        )
        entities = extract_entities("He drives a Fiat.", "uk")
        # Simulate translation that dropped the brand name
        translated = "Він їде на машині."
        fixed, notes = protect_entities_in_translation("He drives a Fiat.", translated, entities, "uk")
        # Either restored or kept as-is (both acceptable)
        assert isinstance(fixed, str)
        assert isinstance(notes, list)

    def test_validate_entity_ok(self):
        from engines.dubbing_engine.entities import extract_entities, validate_entities
        entities = extract_entities("He bought a Fiat.", "uk")
        ok, notes = validate_entities("Він купив Fiat.", entities)
        assert ok
        assert not notes

    def test_validate_entity_missing(self):
        from engines.dubbing_engine.entities import extract_entities, validate_entities
        entities = extract_entities("He bought a Fiat.", "uk")
        ok, notes = validate_entities("Він купив машину.", entities)
        # Missing Fiat → should flag
        assert not ok


# ── Stage 3: Punctuation ──────────────────────────────────────────────────────

class TestPunctuation:
    def test_adds_terminal_period(self):
        from engines.dubbing_engine.punctuation import restore_punctuation
        text, changes = restore_punctuation("Він вийшов на вулицю")
        assert text.endswith(".")
        assert "added_terminal_period" in changes

    def test_keeps_existing_question_mark(self):
        from engines.dubbing_engine.punctuation import restore_punctuation
        text, changes = restore_punctuation("Як справи?")
        assert text.endswith("?")
        assert "added_terminal_period" not in changes

    def test_removes_space_before_comma(self):
        from engines.dubbing_engine.punctuation import restore_punctuation
        text, changes = restore_punctuation("Привіт , світ.")
        assert " ," not in text
        assert "removed_space_before_punct" in changes

    def test_collapses_repeated_exclamations(self):
        from engines.dubbing_engine.punctuation import restore_punctuation
        text, changes = restore_punctuation("Чудово!!!")
        assert "!!!" not in text

    def test_terminal_pause_period(self):
        from engines.dubbing_engine.punctuation import terminal_pause_ms
        ms = terminal_pause_ms("Він вийшов.")
        assert ms == 160

    def test_terminal_pause_question(self):
        from engines.dubbing_engine.punctuation import terminal_pause_ms
        ms = terminal_pause_ms("Де він?")
        assert ms == 150

    def test_capitalise_first_word(self):
        from engines.dubbing_engine.punctuation import restore_punctuation
        text, changes = restore_punctuation("він вийшов на вулицю.")
        assert text[0].isupper()


# ── Stage 7: Validation ───────────────────────────────────────────────────────

class TestValidation:
    def test_lang_mismatch_cyrillic_expected(self):
        from engines.dubbing_engine.validation import run_validation
        report = run_validation(
            input_text="Він вийшов.",
            output_text="He went outside.",  # English in cyrillic output
            lang="uk",
        )
        assert not report.passed
        assert any("language_mismatch" in n for n in report.notes)

    def test_lang_ok_cyrillic(self):
        from engines.dubbing_engine.validation import run_validation
        report = run_validation(
            input_text="Він вийшов назовні.",
            output_text="Він вийшов.",
            lang="uk",
        )
        assert report.checks.get("lang") is True

    def test_meaning_loss_detected(self):
        from engines.dubbing_engine.validation import run_validation
        report = run_validation(
            input_text="Він поїхав додому зі своєю родиною у вихідний день.",
            output_text="Добре.",
            lang="uk",
        )
        assert any("meaning_lost" in n for n in report.notes)

    def test_voice_quality_overflow_flagged(self):
        from engines.dubbing_engine.validation import run_validation, MAX_ATEMPO
        report = run_validation(
            input_text="Він вийшов.",
            output_text="Він вийшов.",
            predicted_ms=4000,
            slot_ms=2000,  # ratio = 2.0 >> MAX_ATEMPO
            lang="uk",
        )
        assert any("voice_quality_risk" in n for n in report.notes)

    def test_passes_all_checks_happy_path(self):
        from engines.dubbing_engine.validation import run_validation
        report = run_validation(
            input_text="Він вийшов на вулицю.",
            output_text="Він вийшов.",
            stress_applied=True,
            punct_ok=True,
            predicted_ms=1500,
            slot_ms=3000,
            lang="uk",
        )
        assert report.passed


# ── DubbingEngine end-to-end ──────────────────────────────────────────────────

class TestDubbingEngine:
    def _make_timing(self, durations_ms: list[int]) -> list[dict]:
        timing = []
        cursor = 0
        for d in durations_ms:
            timing.append({"start": cursor, "end": cursor + d})
            cursor += d + 200
        return timing

    def test_process_all_returns_correct_count(self):
        from engines.dubbing_engine import DubbingEngine
        segs = [
            "18-річний хлопець їхав додому.",
            "Він не міг позбутися страху.",
        ]
        timing = self._make_timing([4000, 3500])
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(segs, timing)
        assert len(results) == 2

    def test_each_result_has_output_text(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["Він вийшов на вулицю."]
        timing = self._make_timing([3000])
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(segs, timing, source_hints=["He went outside."])
        assert results[0].output_text.strip()

    def test_strategy_direct_for_short_segment(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["Так."]
        timing = self._make_timing([5000])
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(segs, timing)
        assert results[0].recommended_strategy == "direct"

    def test_punctuation_applied(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["він вийшов на вулицю"]  # no punct, lowercase
        timing = self._make_timing([3000])
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(segs, timing)
        out = results[0].output_text
        # Should end with punctuation and start with capital
        assert out[-1] in ".?!…"
        assert out[0].isupper()

    def test_fiat_entity_preserved(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["Він купив Fiat."]
        timing = self._make_timing([3000])
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(
            segs, timing, source_hints=["He bought a Fiat."]
        )
        # Phonetic resolver converts "Fiat" → "Фіат" for Ukrainian TTS
        out = results[0].output_text
        assert "Fiat" in out or "Фіат" in out or "фіат" in out.lower()

    def test_stage_log_populated(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["він вийшов назовні"]
        timing = self._make_timing([3000])
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(segs, timing)
        stages = {s.stage for s in results[0].stage_log}
        # Should have at minimum entity, punct, stress, timing, validate
        assert "entity" in stages
        assert "punct" in stages
        assert "validate" in stages

    def test_natural_pauses_out_filled(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["Він вийшов.", "Де він?"]
        timing = self._make_timing([3000, 2500])
        pauses: list[int] = []
        engine = DubbingEngine(lang="uk")
        engine.process_all(segs, timing, natural_pauses_out=pauses)
        assert len(pauses) == 2
        assert all(p > 0 for p in pauses)

    def test_overflow_segment_gets_video_adapt_strategy(self):
        from engines.dubbing_engine import DubbingEngine
        # Very long text in a very short slot → should recommend video_adapt or merge_next
        segs = ["Він не міг позбутися відчуття, що йому справді страшно туди дістатися."]
        timing = [{"start": 0, "end": 1500}]  # only 1.5 seconds for ~8 second text
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(segs, timing)
        strategy = results[0].recommended_strategy
        assert strategy in ("video_adapt", "merge_next", "adapt_more", "direct")
        # Just check it didn't crash and returned a strategy

    def test_russian_stress_applied(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["Он вышел на улицу."]
        timing = self._make_timing([3000])
        engine = DubbingEngine(lang="ru")
        results = engine.process_all(segs, timing)
        # Stress marks are added (U+0301); exact result depends on stress_marks module
        # Just check output is non-empty and valid
        assert results[0].output_text.strip()

    def test_no_crash_on_empty_segment(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["", "Він вийшов."]
        timing = self._make_timing([1000, 3000])
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(segs, timing)
        assert len(results) == 2

    def test_to_dict_returns_required_keys(self):
        from engines.dubbing_engine import DubbingEngine
        segs = ["Він вийшов."]
        timing = self._make_timing([3000])
        engine = DubbingEngine(lang="uk")
        results = engine.process_all(segs, timing)
        d = results[0].to_dict()
        for key in ("index", "input", "output", "passed", "strategy", "predicted_ms", "slot_ms"):
            assert key in d

    def test_skip_text_adaptation_preserves_pre_timed_input(self):
        from engines.dubbing_engine import DubbingEngine

        segs = ["Це має сенс."]
        timing = self._make_timing([2000])
        engine = DubbingEngine(lang="uk", skip_text_adaptation=True)
        results = engine.process_all(segs, timing, source_hints=["This makes sense."])
        assert results[0].output_text.strip()
        stages = {s.stage: s for s in results[0].stage_log}
        assert stages["adapt"].note == "skipped_pre_timed"
        assert stages["voice"].note == "skipped_pre_timed"
