"""Tests — semantic optimizer with time budget (TZ §1–4)."""

from __future__ import annotations

from engines.semantic_meaning import (
    check_critical_entities,
    compute_meaning_loss_score,
    validate_transformation_chain,
)
from engines.semantic_optimizer import compute_time_budget, optimize_for_time_budget
from engines.translation_quality import build_quality_analysis, diagnose_raw_mt


def test_compute_time_budget_fits():
    budget = compute_time_budget("Коротко.", slot_ms=5000, tgt_lang="ru")
    assert budget.fits is True
    assert budget.delta_ms == 0


def test_optimize_skips_when_fits():
    text = "Короткая реплика."
    result = optimize_for_time_budget(
        text,
        source_hint="Short line.",
        slot_ms=8000,
        tgt_lang="ru",
    )
    assert result.text == text
    assert result.changed is False
    assert result.stopped_reason == "fits_no_change"
    assert not result.stages


def test_optimize_runs_stages_when_overflow():
    long_text = " ".join(["слово"] * 25) + "."
    result = optimize_for_time_budget(
        long_text,
        source_hint="word " * 10,
        slot_ms=1500,
        tgt_lang="ru",
        allow_llm=False,
    )
    assert result.budget.fits is False or result.changed
    assert isinstance(result.stages, list)


def test_check_critical_entities_george_jr():
    src = "George Jr. could not help but feel dread."
    bad = "Він не міг позбутися відчуття."
    errors = check_critical_entities(src, bad)
    assert any(e["category"] == "named_entity" for e in errors)


def test_check_critical_entities_george_translated_ok():
    src = "George Jr. could not help but feel dread."
    good = "Джордж-молодший не міг позбутися відчуття."
    errors = check_critical_entities(src, good)
    assert errors == []


def test_validate_transformation_chain_george_translated_ok():
    ok, reason, _ = validate_transformation_chain(
        original="George Jr. could not help but feel dread.",
        raw_mt="Джордж-молодший не міг позбутися відчуття.",
        semantic="Джордж-молодший не міг позбутися відчуття.",
        final_tts="Джордж-молодший не міг позбутися відчуття.",
        source="George Jr. could not help but feel dread.",
    )
    assert ok
    assert reason == "ok"


def test_validate_transformation_chain_ok():
    ok, reason, _ = validate_transformation_chain(
        original="Hello George Jr.",
        raw_mt="Привет, George Jr.",
        semantic="Привет, George Jr.",
        final_tts="Привет, George Jr.",
        source="Hello George Jr.",
    )
    assert ok
    assert reason == "ok"


def test_validate_transformation_chain_accepts_natural_paraphrase_vs_raw_mt():
    """Semantic improvement vs Raw MT must pass when meaning preserved vs English."""
    source = (
        "But, as he was driving, George Jr. could not help but feel like "
        "he was really dreading actually getting there."
    )
    raw_mt = (
        "але, коли він їхав, Джордж-молодший не міг не відчувати, "
        "що він дійсно боїться потрапити туди."
    )
    semantic = (
        "але, коли він їхав, Джордж-молодший його не полишала тривога, "
        "що йому справді страшно туди дістатися."
    )
    ok, reason, details = validate_transformation_chain(
        original=source,
        raw_mt=raw_mt,
        semantic=semantic,
        final_tts=semantic,
        source=source,
    )
    assert ok, f"expected ok, got {reason} details={details}"
    assert reason == "ok"
    assert details["meaning_preservation_score"] >= 0.9
    assert details.get("raw_mt_divergence") is not None
    assert details.get("change_reasons")


def test_validate_transformation_chain_rejects_empty_output():
    ok, reason, _ = validate_transformation_chain(
        original="that George Jr. was ejected from the car but he had survived.",
        raw_mt="",
        semantic="",
        final_tts="",
        source="that George Jr. was ejected from the car but he had survived.",
    )
    assert not ok
    assert reason in ("empty_output", "meaning_loss_exceeded", "entity_loss")


def test_compute_meaning_loss_ignores_raw_mt_baseline():
    source = "George Jr. drove home."
    raw_mt = "Джордж-молодший їхав додому дуже довгим реченням з зайвими словами."
    improved = "Джордж-молодший їхав додому."
    loss = compute_meaning_loss_score(source, raw_mt, improved)
    assert loss <= 0.15


def test_check_critical_entities_usc_translated_ok():
    src = "George Jr. applied to the University of Southern California."
    good = (
        "Джордж-молодший подав заявку до Університету Південної Каліфорнії."
    )
    errors = check_critical_entities(src, good)
    assert errors == []


def test_format_runtime_pipeline_block_skip_reasons():
    from engines.pipeline_integrity.semantic_validation_openddf import (
        build_runtime_pipeline,
        format_runtime_pipeline_block,
        summarize_runtime_pipeline,
    )

    runtime = build_runtime_pipeline(
        {
            "source_segments": ["Hello"],
            "translation_audits": [
                {
                    "engine": "marian",
                    "raw_translation": "Привет",
                    "naturalized_text": "Привет",
                    "final_text": "Привет",
                    "naturalizer_applied": False,
                    "naturalizer_executed": True,
                    "timing_aware_applied": False,
                    "timing_aware_executed": True,
                }
            ],
            "pipeline_stages": {
                "natural_translation": {
                    "enabled": True,
                    "executed": True,
                    "applied": False,
                    "skip_reason": "no_changes_needed",
                },
                "timing_aware_translation": {
                    "enabled": True,
                    "executed": True,
                    "applied": False,
                    "skip_reason": "fits_without_change",
                },
            },
            "timing_map": [{"start": 0, "end": 2000}],
        }
    )
    summary = summarize_runtime_pipeline(runtime)
    text = format_runtime_pipeline_block(runtime)
    nat = next(r for r in summary if r["key"] == "natural_translation")
    tat = next(r for r in summary if r["key"] == "timing_aware_translation")
    assert nat["executed"] is True
    assert nat["applied"] is False
    assert nat["skip_reason"] == "no_changes_needed"
    assert tat["executed"] is True
    assert tat["skip_reason"] == "fits_without_change"
    assert "skip_reason=no_changes_needed" in text
    assert "skip_reason=fits_without_change" in text


def test_build_quality_analysis_over_shortening_detail():
    long_raw = (
        "Але, коли він їхав, Джордж-молодший не міг позбутися відчуття, "
        "що йому справді страшно їхати туди, і це його дуже турбувало."
    )
    short_final = "Але, коли він їхав, Джордж-молодший не міг позбутися відчуття."
    qa = build_quality_analysis(
        original="But, as he was driving, George Jr. could not help but feel dread.",
        raw=long_raw,
        naturalized=long_raw,
        final=short_final,
        tts_text=short_final,
        source_lang="en",
        target_lang="uk",
    )
    over = [r for r in qa["reasons"] if r["code"] == "over_shortening"]
    assert over
    assert "Сокращено" in over[0]["summary"]
    assert "meaning_loss_risk" in over[0]["detail"]


def test_build_pipeline_stage_report_natural_and_timing():
    from engines.pipeline_integrity.semantic_validation_openddf import (
        build_pipeline_stage_report,
    )
    from engines.timing_aware_translation import TimingAwareRecord

    stages = build_pipeline_stage_report(
        raw_by_index=["Hello", "Long text here"],
        post_naturalizer=["Привет", "Довгий текст тут"],
        naturalized=["Привет", "Коротко."],
        timing_map=[{"start": 0, "end": 2000}, {"start": 2100, "end": 4000}],
        timing_aware_records=[
            TimingAwareRecord(index=0, adapted=False),
            TimingAwareRecord(index=1, adapted=True),
        ],
    )
    assert stages["natural_translation"]["executed"] is True
    assert stages["natural_translation"]["applied"] is True
    assert stages["timing_aware_translation"]["executed"] is True
    assert stages["timing_aware_translation"]["applied"] is True
    assert stages["timing_aware_translation"]["segments_adapted"] == 1


def test_diagnose_raw_mt_empty():
    diag = diagnose_raw_mt(
        "Hello world",
        "",
        meta={"mt_failed": True, "engine": "marian"},
    )
    assert diag
    assert diag["cause"] == "translation_engine_failed"
    assert diag["severity"] == "error"
