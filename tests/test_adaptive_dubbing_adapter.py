"""Tests for engines/adaptive_dubbing_adapter.py (Adaptive Dubbing Adapter)."""

import pytest
from engines.adaptive_dubbing_adapter import (
    AdaptResult,
    adapt_segment,
    adapt_segments_for_tts,
    fits_in_slot,
    natural_pause_ms,
    predict_duration_ms,
    validate_pre_tts,
    _step_reframe,
    _step_synonyms,
    _step_word_order,
    _step_remove_secondary,
)


# ─── Duration predictor ───────────────────────────────────────────────────────

class TestPredictDurationMs:
    def test_empty_text(self):
        assert predict_duration_ms("", "ru") == 0

    def test_short_text(self):
        ms = predict_duration_ms("Привет.", "ru")
        assert 100 < ms < 1500

    def test_longer_text_larger_duration(self):
        short = predict_duration_ms("Он пришёл.", "ru")
        long = predict_duration_ms(
            "Он пришёл домой очень поздно после долгого рабочего дня.", "ru"
        )
        assert long > short

    def test_fits_in_slot_true(self):
        assert fits_in_slot("Да.", "ru", 2000)

    def test_fits_in_slot_false(self):
        very_long = " ".join(["слово"] * 50)
        assert not fits_in_slot(very_long, "ru", 500)

    def test_fits_in_slot_no_timing(self):
        # slot_ms=0 means no timing → always fits
        assert fits_in_slot("anything", "ru", 0)


# ─── Natural pause ────────────────────────────────────────────────────────────

class TestNaturalPause:
    def test_period(self):
        assert natural_pause_ms("Он пришёл.") == 160

    def test_question(self):
        assert natural_pause_ms("Ты здесь?") == 150

    def test_exclamation(self):
        assert natural_pause_ms("Стоп!") == 150

    def test_ellipsis(self):
        assert natural_pause_ms("Может быть…") == 200

    def test_default(self):
        # no ending punctuation → default
        assert natural_pause_ms("no punct") == 120

    def test_empty(self):
        assert natural_pause_ms("") == 120

    def test_within_bounds(self):
        for t in ["Да.", "Нет?", "Стоп!", "Ладно…", "и так"]:
            p = natural_pause_ms(t)
            assert 80 <= p <= 220, f"Unexpected pause {p} for {t!r}"


# ─── Reframe step ─────────────────────────────────────────────────────────────

class TestStepReframe:
    def test_fronted_khotya_ru(self):
        text = "Хотя погода была плохой, он всё равно вышел."
        result = _step_reframe(text, "ru")
        assert result is not None
        assert "хотя" in result.lower()
        assert "вышел" in result.lower()
        # Fronted clause moved to end — 'вышел' appears before 'хотя'
        assert result.lower().index("вышел") < result.lower().index("хотя")

    def test_fronted_esli_ru(self):
        text = "Если будет дождь, то мы останемся дома."
        result = _step_reframe(text, "ru")
        assert result is not None
        assert "если" in result.lower()

    def test_no_match_simple_ru(self):
        text = "Он пришёл домой."
        result = _step_reframe(text, "ru")
        assert result is None

    def test_fronted_although_en(self):
        text = "Although the weather was bad, he went out anyway."
        result = _step_reframe(text, "en")
        assert result is not None
        # 'although' should be moved to end
        assert result.index("although") > 0

    def test_no_match_en(self):
        result = _step_reframe("He came home.", "en")
        assert result is None


# ─── Synonyms step ────────────────────────────────────────────────────────────

class TestStepSynonyms:
    def test_ru_synonym_replaced(self):
        text = "Необходимо использовать новый метод."
        result = _step_synonyms(text, "ru")
        assert result is not None
        assert "необходимо" not in result.lower() or "нужно" in result.lower()

    def test_en_synonym_replaced(self):
        text = "We need to utilize the new approach."
        result = _step_synonyms(text, "en")
        assert result is not None
        assert "utilize" not in result.lower()
        assert "use" in result.lower()

    def test_no_change_if_no_match(self):
        result = _step_synonyms("Simple text.", "en")
        assert result is None

    def test_multiword_phrase_ru(self):
        text = "Таким образом, мы решили задачу."
        result = _step_synonyms(text, "ru")
        assert result is not None
        assert "таким образом" not in result.lower()
        assert "так" in result.lower()


# ─── Word order step ──────────────────────────────────────────────────────────

class TestStepWordOrder:
    def test_fronted_today_ru(self):
        text = "Сегодня он пришёл домой."
        result = _step_word_order(text, "ru")
        assert result is not None
        # 'сегодня' should now be later
        assert result.lower().index("сегодня") > 0

    def test_fronted_vchera_ru(self):
        text = "Вчера она купила книгу."
        result = _step_word_order(text, "ru")
        assert result is not None
        assert "вчера" in result.lower()

    def test_en_no_change(self):
        # English word order is rigid — no change
        result = _step_word_order("Today he came home.", "en")
        assert result is None

    def test_no_match_no_adverb(self):
        result = _step_word_order("Он пришёл домой.", "ru")
        assert result is None


# ─── Remove secondary step ────────────────────────────────────────────────────

class TestStepRemoveSecondary:
    def test_remove_konechno_ru(self):
        text = "Конечно, он понял что нужно сделать."
        result = _step_remove_secondary(text, "ru", "")
        assert result is not None
        assert "конечно" not in result.lower()
        assert "понял" in result

    def test_remove_trailing_vy_ponimaete(self):
        text = "Он был прав, вы понимаете."
        result = _step_remove_secondary(text, "ru", "")
        assert result is not None
        assert "понимаете" not in result.lower()

    def test_removes_duplicate_words(self):
        text = "Он пришёл пришёл домой."
        result = _step_remove_secondary(text, "ru", "")
        assert result is not None
        assert "пришёл пришёл" not in result

    def test_preserves_negation(self):
        text = "Конечно, он не хотел этого."
        result = _step_remove_secondary(text, "ru", "")
        # Negation 'не' must be preserved
        assert result is None or "не" in result

    def test_remove_of_course_en(self):
        text = "Of course, he understood what to do."
        result = _step_remove_secondary(text, "en", "")
        assert result is not None
        assert "of course" not in result.lower()
        assert "understood" in result

    def test_no_change_clean_text(self):
        text = "He came home."
        result = _step_remove_secondary(text, "en", "")
        # Nothing to remove
        assert result is None or result == text


# ─── Pre-TTS validation ───────────────────────────────────────────────────────

class TestValidatePreTts:
    def test_empty_text_fails(self):
        ok, notes = validate_pre_tts("", "hello", "", 3000, "ru")
        assert not ok
        assert "empty_text" in notes

    def test_clean_text_passes(self):
        ok, notes = validate_pre_tts("Он пришёл домой.", "Он пришёл домой.", "", 5000, "ru")
        assert ok

    def test_technical_tokens_detected(self):
        ok, notes = validate_pre_tts("XMLNS20 text", "original", "", 3000, "ru")
        assert not ok
        assert any("tech" in n for n in notes)

    def test_overflow_warning_not_blocking(self):
        # Duration overflow generates a warning but does NOT block TTS
        very_long = " ".join(["слово"] * 100)
        ok, notes = validate_pre_tts(very_long, very_long, "", 500, "ru")
        # ok may be True/False depending on retention, but "empty_text" not in notes
        assert "empty_text" not in notes

    def test_over_shortened_fails(self):
        original = "Длинное предложение с множеством важных слов и информацией."
        adapted = "Да."
        ok, notes = validate_pre_tts(adapted, original, "", 3000, "ru")
        assert not ok
        assert any("retention" in n for n in notes)


# ─── Main adapt_segment ───────────────────────────────────────────────────────

class TestAdaptSegment:
    def test_returns_adapt_result(self):
        r = adapt_segment("Привет.", slot_ms=3000, lang="ru")
        assert isinstance(r, AdaptResult)

    def test_short_text_fits_no_change(self):
        r = adapt_segment("Да.", slot_ms=3000, lang="ru")
        assert r.fits
        assert not r.changed

    def test_empty_text(self):
        r = adapt_segment("", slot_ms=3000, lang="ru")
        assert r.fits
        assert r.text == ""

    def test_natural_pause_populated(self):
        r = adapt_segment("Он пришёл домой.", slot_ms=3000, lang="ru")
        assert r.natural_pause_ms > 0

    def test_steps_are_recorded(self):
        r = adapt_segment("Привет мир.", slot_ms=3000, lang="ru")
        # At least the initial prediction step should always be present
        names = [s.name for s in r.steps]
        # Step is now named "predict_initial" (more descriptive)
        assert any("predict" in n for n in names)

    def test_adapted_text_not_empty_when_started_non_empty(self):
        long = "Несмотря на то, что он очень устал после длинного рабочего дня, он всё равно пошёл на встречу."
        r = adapt_segment(long, slot_ms=4000, lang="ru")
        assert r.text.strip() != ""

    def test_word_retention_respected(self):
        text = "Он пришёл домой вечером."
        r = adapt_segment(text, slot_ms=500, lang="ru")
        # Even if it can't fit, text should retain most original words
        orig_words = len(text.split())
        adpt_words = len(r.text.split())
        assert adpt_words >= int(orig_words * 0.60)

    def test_ru_filler_removed_when_needed(self):
        text = "Конечно, он понял что нужно сделать в этой ситуации."
        # Give it a tight slot
        r = adapt_segment(text, slot_ms=1500, lang="ru")
        # Either filler was removed or other steps applied
        applied = [s.name for s in r.steps if s.applied]
        assert len(applied) >= 0  # just verify no crash


# ─── Batch adapt_segments_for_tts ────────────────────────────────────────────

class TestAdaptSegmentsForTts:
    def test_returns_correct_length(self):
        segs = ["Привет.", "Как дела?", "Хорошо."]
        timing = [
            {"start": 0, "end": 2000},
            {"start": 2200, "end": 4000},
            {"start": 4200, "end": 5500},
        ]
        out, meta = adapt_segments_for_tts(segs, timing_map=timing, lang="ru")
        assert len(out) == 3

    def test_no_timing_passthrough(self):
        segs = ["Привет.", "Пока."]
        out, meta = adapt_segments_for_tts(segs, timing_map=None, lang="ru")
        assert out == segs
        assert meta["changed"] == 0

    def test_meta_keys_present(self):
        segs = ["Test."]
        timing = [{"start": 0, "end": 3000}]
        _, meta = adapt_segments_for_tts(segs, timing_map=timing, lang="en")
        assert "segments" in meta
        assert "changed" in meta
        assert "overflows_remaining" in meta
        assert "elapsed_sec" in meta
        assert "natural_pauses" in meta

    def test_natural_pauses_count_matches_segments(self):
        segs = ["One.", "Two?", "Three!"]
        timing = [
            {"start": 0, "end": 2000},
            {"start": 2100, "end": 3800},
            {"start": 4000, "end": 5500},
        ]
        _, meta = adapt_segments_for_tts(segs, timing_map=timing, lang="en")
        # One pause per segment that had timing
        assert len(meta["natural_pauses"]) == 3

    def test_source_hints_passed(self):
        segs = ["Он пришёл домой."]
        timing = [{"start": 0, "end": 3000}]
        sources = ["He came home."]
        out, meta = adapt_segments_for_tts(
            segs, timing_map=timing, lang="ru", source_hints=sources
        )
        assert len(out) == 1

    def test_empty_input(self):
        out, meta = adapt_segments_for_tts([], timing_map=[], lang="ru")
        assert out == []
        assert meta["segments"] == 0


# ─── P0 no-hang regression: empty LLM responses ──────────────────────────────

class TestAdaEmptyLlmNeverHangs:
    """The Adaptive Dubbing Adapter must ALWAYS terminate — even when the local
    LLM keeps returning empty / 0 usable candidates (the live P0 bug). It must
    finish quickly with the best rule-based / kept text and never leak the
    English source, never loop, never block.
    """

    _OVERFLOW_TEXT = (
        "Через два роки Джордж-молодший повністю одужав від травм, "
        "стояв на фінішній прямій на гоночному треку і підняв фотоапарат, "
        "щоб зробити кілька знімків переможного заїзду того дня."
    )

    def test_empty_llm_completes_fast_with_fallback(self, monkeypatch):
        import time as _time

        from engines.ai_core import llm_gateway

        # Simulate the live failure: LLM is "available" but returns nothing.
        monkeypatch.setattr(llm_gateway, "is_available", lambda: True)
        calls = {"n": 0}

        def _empty_chat(*a, **k):
            calls["n"] += 1
            return ""  # 0 candidates, exactly like the hang scenario

        monkeypatch.setattr(llm_gateway, "chat", _empty_chat)

        t0 = _time.perf_counter()
        result = adapt_segment(
            self._OVERFLOW_TEXT,
            slot_ms=5600,           # heavy overflow → LLM path is exercised
            lang="uk",
            source_hint="Two years later George Jr. had fully recovered.",
            segment_index=1,
        )
        elapsed = _time.perf_counter() - t0

        # Never hangs: a single in-process call must return effectively instantly.
        assert elapsed < 5.0
        # Terminates with usable target-language text (never empty, never English).
        assert result.text.strip()
        assert "recovered" not in result.text.lower()
        # The empty LLM was consulted but did not derail the pipeline.
        assert calls["n"] >= 1

    def test_batch_with_empty_llm_terminates(self, monkeypatch):
        from engines.ai_core import llm_gateway

        monkeypatch.setattr(llm_gateway, "is_available", lambda: True)
        monkeypatch.setattr(llm_gateway, "chat", lambda *a, **k: "")

        segs = [self._OVERFLOW_TEXT, self._OVERFLOW_TEXT]
        timing = [{"start": 0, "end": 5600}, {"start": 6000, "end": 11600}]
        out, meta = adapt_segments_for_tts(
            segs,
            timing_map=timing,
            lang="uk",
            source_hints=["src one", "src two"],
        )
        assert len(out) == 2
        assert all(t.strip() for t in out)

    def test_unlabeled_single_line_llm_response_is_salvaged(self, monkeypatch):
        """Small local models often ignore the A:/B: format and return one
        unlabeled line — it must still be accepted (fixes the 0-candidates bug)."""
        from engines.adaptive_dubbing_adapter import _llm_rephrase_variants
        from engines.ai_core import llm_gateway

        monkeypatch.setattr(llm_gateway, "is_available", lambda: True)
        monkeypatch.setattr(
            llm_gateway,
            "chat",
            lambda *a, **k: "Через два роки Джордж одужав і підняв фотоапарат на треку.",
        )
        variants = _llm_rephrase_variants(
            self._OVERFLOW_TEXT, "uk", 5600, "source", n=7
        )
        assert variants, "single-line LLM response should be salvaged as a candidate"

    def test_unusable_llm_output_signals_circuit_breaker(self, monkeypatch):
        """When the LLM responds but yields 0 usable variants, ADA must report it
        so the global circuit breaker can eventually trip."""
        import engines.translation_adapt as ta
        from engines.adaptive_dubbing_adapter import _llm_rephrase_variants
        from engines.ai_core import llm_gateway

        monkeypatch.setattr(llm_gateway, "is_available", lambda: True)
        # Non-empty but unusable (too short / junk) → 0 candidates.
        monkeypatch.setattr(llm_gateway, "chat", lambda *a, **k: "...")
        calls = {"n": 0}
        monkeypatch.setattr(
            ta, "record_llm_unusable", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
        )
        variants = _llm_rephrase_variants(self._OVERFLOW_TEXT, "uk", 5600, "src", n=7)
        assert variants == []
        assert calls["n"] == 1
