"""PSA7 — Diagnostics truth: reason mapping + summary metrics."""

from __future__ import annotations

from engines.pipeline_integrity.honest_diagnostics import (
    apply_honest_reasons,
    collect_honest_summary,
    collect_stability_metrics,
    map_segment_algorithm_reason,
    residual_overflow_ms,
    sanitize_algorithm_reason,
    set_reason,
)


def test_psa7_split_reason_fields():
    seg = {"segment_id": "a" * 32, "slot_ms": 1000, "playback_duration": 1500}
    set_reason(seg, "text_adaptation_reason", "semantic_shortening")
    set_reason(seg, "audio_strategy_reason", "AudioStrategyNoTextRewrite")
    seg["residual_overflow_ms"] = residual_overflow_ms(seg)
    summary = collect_honest_summary(seg)
    assert summary["text_adaptation_reason"] == "semantic_shortening"
    assert summary["audio_strategy_reason"] == "AudioStrategyNoTextRewrite"
    assert summary["residual_overflow_ms"] == 500


def test_psa7_forbid_semantic_shorten_for_audio_only():
    seg = {
        "segment_id": "b" * 32,
        "slot_ms": 2000,
        "playback_duration": 2100,
        "fitted_file": "fitted.wav",
        "overflow_decision": {"chosen": "trim"},
        "adaptation_stages": ["overflow_strategy:trim"],
        "decision_trace": ["TTS:SKIPPED:AudioStrategyNoTextRewrite"],
        # Dishonest legacy stamp
        "text_adaptation_trace": {"executed": True, "reasons": ["trim"]},
        "algorithm_reason": (
            "post_tts_text_adaptation: semantic shorten + TTS regen until slot fit"
        ),
        "text_adaptation_reason": "semantic_shortening",
    }
    summary = apply_honest_reasons(seg)
    assert "semantic shorten" not in summary["algorithm_reason"].lower()
    assert "semantic_shorten" not in summary["algorithm_reason"].lower()
    assert summary["audio_strategy_reason"]
    assert "AudioStrategy" in summary["audio_strategy_reason"] or "trim" in (
        summary["audio_strategy_reason"].lower()
    )
    # text adaptation cleared when only audio ran
    assert summary["text_adaptation_reason"] in ("",)


def test_psa7_sanitize_algorithm_reason_unit():
    cleaned = sanitize_algorithm_reason(
        "post_tts_text_adaptation: semantic shorten + TTS regen until slot fit",
        audio_strategy_reason="AudioStrategyNoTextRewrite",
        text_adaptation_reason="",
    )
    assert "semantic shorten" not in cleaned.lower()
    assert cleaned == "AudioStrategyNoTextRewrite"


def test_psa7_real_semantic_shorten_allowed():
    seg = {
        "segment_id": "c" * 32,
        "slot_ms": 3000,
        "playback_duration": 2900,
        "text_adaptation_trace": {
            "executed": True,
            "reasons": ["semantic_shortening"],
            "stages": ["llm_adapt"],
        },
        "adaptation_stages": ["semantic_shortening"],
    }
    reason = map_segment_algorithm_reason(seg, timing_aware={})
    assert "semantic shorten" in reason.lower() or seg.get("text_adaptation_reason")


def test_psa7_stability_metrics_split_overflow_vs_placement():
    segs = [
        {
            "segment_id": "1" * 32,
            "plain_text": "And at",
            "original": "And at",
            "translated_text": "А",
            "final_tts_text": "Б",  # identity mismatch
            "slot_ms": 400,  # micro
            "start_ms": 0,
            "playback_duration": 900,  # residual overflow 500
        },
        {
            "segment_id": "2" * 32,
            "plain_text": "Next line with enough words here for density.",
            "translated_text": "X",
            "final_tts_text": "X",
            "slot_ms": 5000,
            "start_ms": 500,  # placement overlap vs prev end 900
            "playback_duration": 1000,
        },
        {
            "segment_id": "3" * 32,
            "plain_text": "ok",
            "translated_text": "ok",
            "final_tts_text": "ok",
            "slot_ms": 2000,
            "start_ms": 10000,
            "playback_duration": 1900,  # no residual overflow
        },
    ]
    metrics = collect_stability_metrics(segs)
    assert metrics["identity_mismatch_count"] >= 1
    assert metrics["micro_slot_count"] >= 1
    assert metrics["residual_overflow_count"] >= 1
    assert metrics["placement_overlap_count"] >= 1
    # Disambiguation note present
    assert "residual_overflow" in metrics["notes"]
    assert "placement_overlap" in metrics["notes"]
    # residual ≠ placement conceptually: both can be >0 independently
    assert metrics["residual_overflow_count"] != metrics.get("segment_overflow_count", -1)


def test_psa7_set_reason_rejects_cross_field_lies():
    seg = {"segment_id": "d" * 32}
    set_reason(seg, "audio_strategy_reason", "semantic_shortening")
    assert seg["audio_strategy_reason"] == "AudioStrategyNoTextRewrite"
    set_reason(seg, "text_adaptation_reason", "AudioStrategyNoTextRewrite")
    assert "text_adaptation_reason" not in seg or not seg.get("text_adaptation_reason")
