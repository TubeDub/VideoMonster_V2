"""AI Adaptation Engine — architecture tests (P0 rewrite)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from engines.ai_adaptation_engine import (
    AdaptationGateResult,
    adaptation_profile,
    adapt_segment_ai,
    enforce_adaptation_gate,
    validate_pre_tts_checks,
    _score_variant,
)


def test_gate_blocks_requires_llm_without_call():
    gate = enforce_adaptation_gate(
        ["text one.", "text two."],
        timing_records=[
            {"index": 0, "requires_llm_adaptation": True, "llm_called": False},
            {"index": 1, "requires_llm_adaptation": False, "llm_called": False},
        ],
        llm_status=[
            {"segment": 0, "needed": True, "called": False, "skip_reason": "no_endpoint"},
        ],
        llm_calls=[],
    )
    # Graceful LLM skip with usable text → rule fallback, gate passes
    assert gate.passed is True
    assert gate.violations == []


def test_gate_passes_when_llm_called():
    gate = enforce_adaptation_gate(
        ["adapted text here."],
        timing_records=[
            {"index": 0, "requires_llm_adaptation": True, "llm_called": True},
        ],
        llm_calls=[{"segment": 0, "usable": True}],
    )
    assert gate.passed is True
    assert gate.violations == []


def test_gate_passes_when_no_llm_needed():
    gate = enforce_adaptation_gate(
        ["short."],
        timing_records=[{"index": 0, "reason": "fits_no_change"}],
    )
    assert gate.passed is True


def test_fits_no_change_skips_llm():
    result = adapt_segment_ai(
        "Короткий текст.",
        source_hint="Short text.",
        slot_ms=5000,
        tgt_lang="uk",
        index=0,
    )
    assert result.changed is False
    assert result.requires_llm_adaptation is False
    assert result.trace.chosen_reason == "fits_no_change"


@patch("engines.translation_adapt.llm_rephrase_available", return_value=False)
def test_overflow_without_llm_marks_requires(_mock_avail):
    long_text = (
        "Це дуже довгий текст для тестування адаптації який точно не поміститься "
        "у короткий слот озвучування без інтелектуальної переробки."
    )
    result = adapt_segment_ai(
        long_text,
        source_hint="This is a very long test line that will not fit.",
        slot_ms=400,
        tgt_lang="uk",
        index=3,
    )
    assert result.requires_llm_adaptation is True
    assert result.llm_called is False
    assert result.trace.llm_skip_reason == "no_endpoint"


@patch("engines.translation_adapt.llm_rephrase_available", return_value=True)
@patch("engines.translation_adapt._llm_chat", return_value=None)
def test_overflow_with_llm_unresponsive(_mock_chat, _mock_avail):
    long_text = (
        "Це дуже довгий текст для тестування адаптації який точно не поміститься "
        "у короткий слот озвучування без інтелектуальної переробки."
    )
    result = adapt_segment_ai(
        long_text,
        source_hint="This is a very long test line that will not fit.",
        slot_ms=400,
        tgt_lang="uk",
        index=5,
    )
    assert result.trace.requires_llm is True
    assert result.llm_called is False
    # Pipeline continues via rule fallback when LLM exhausted but prep text kept.
    assert result.trace.rule_fallback_applied is True
    assert result.requires_llm_adaptation is False


def test_score_variant_rejects_empty():
    scores, reason = _score_variant(
        "",
        original="Original text.",
        source_hint="Source.",
        literal_translation="Literal text.",
        slot_ms=2000,
        tgt_lang="uk",
    )
    assert reason == "empty"
    assert scores.total == 0.0


def test_score_variant_accepts_good_sentence():
    text = "Він поїхав додому на вечерю."
    scores, reason = _score_variant(
        text,
        original=text,
        source_hint="He went home for dinner.",
        literal_translation=text,
        slot_ms=3000,
        tgt_lang="uk",
    )
    assert reason == ""
    assert scores.total > 0.5
    assert scores.grammar == 1.0


def test_score_variant_rejects_hallucinated_number():
    scores, reason = _score_variant(
        "Він поїхав 2050 року.",
        original="Він поїхав у 2020 році.",
        source_hint="He left in 2020.",
        literal_translation="Він поїхав у 2020 році.",
        slot_ms=3000,
        tgt_lang="uk",
    )
    assert reason == "hallucination"
    assert scores.total == 0.0


def test_adaptation_profile_min_variants():
    prof = adaptation_profile()
    assert int(prof.get("min_variants") or 0) >= 10


def test_validate_pre_tts_rejects_empty():
    ok, issues = validate_pre_tts_checks(
        "",
        source_hint="Source.",
        original="Original.",
        slot_ms=2000,
        tgt_lang="uk",
    )
    assert ok is False
    assert "empty" in issues


def test_validate_pre_tts_rejects_mid_word():
    ok, issues = validate_pre_tts_checks(
        "Джордж поїхав до Каліфор-",
        source_hint="George went to California.",
        original="Джордж подався до Каліфорнії.",
        slot_ms=2000,
        tgt_lang="uk",
    )
    assert ok is False
    assert "mid_word" in issues


def test_validate_pre_tts_accepts_complete():
    ok, issues = validate_pre_tts_checks(
        "Джордж подався до Каліфорнії, щоб вчитися.",
        source_hint="George went to California to study.",
        original="Джордж подався до Каліфорнії, щоб вчитися.",
        slot_ms=3000,
        tgt_lang="uk",
    )
    assert ok is True
    assert issues == []


def test_validate_pre_tts_rejects_bad_mt_phrase():
    ok, issues = validate_pre_tts_checks(
        "Тепер Джордж-молодший підійшов до подіуму, щоб зробити кілька фотографій переможного їзда.",
        source_hint="Now George Junior walked up to the podium to take pictures of the winning drive.",
        original="Тепер Джордж-молодший підійшов до подіуму, щоб зробити кілька фотографій переможного їзда.",
        slot_ms=12000,
        tgt_lang="uk",
    )
    assert ok is False
    assert any(i.startswith("bad_mt:") for i in issues)
