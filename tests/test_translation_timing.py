"""Tests for translation stage timing breakdown."""

from engines.translation_timing import (
    TranslationTimingBreakdown,
    build_breakdown,
    format_duration_clock,
    format_duration_hms,
    format_duration_verbose,
    _build_live_timing_payload,
)


def test_format_duration_clock():
    assert format_duration_clock(0) == "0:00"
    assert format_duration_clock(42) == "0:42"
    assert format_duration_clock(134) == "2:14"
    assert format_duration_clock(3661) == "1:01:01"


def test_format_duration_hms():
    assert format_duration_hms(0) == "00:00:00"
    assert format_duration_hms(18) == "00:00:18"
    assert format_duration_hms(461) == "00:07:41"


def test_format_duration_verbose_ru():
    assert "2 мин" in format_duration_verbose(134, lang="ru")
    assert format_duration_verbose(18, lang="ru") == "18 сек"


def test_ui_buckets_three_bars():
    br = build_breakdown(
        marian_sec=134.0,
        naturalizer_sec=420.0,
        llm_ms_total=408_000.0,
        restore_sec=5.0,
        validation_sec=18.0,
        semantic_sec=6.0,
        timing_aware_sec=4.0,
        segment_count=312,
    )
    buckets = br.ui_buckets()
    assert set(buckets.keys()) == {"marian_mt", "llm_adaptation", "post_processing"}
    assert buckets["marian_mt"] == 134.0
    assert buckets["llm_adaptation"] == 408.0
    assert buckets["post_processing"] == 45.0


def test_to_dict_includes_labels_and_stats():
    br = TranslationTimingBreakdown(
        marian_sec=18.2,
        llm_adaptation_sec=461.0,
        segment_count=312,
    )
    d = br.to_dict()
    assert d["ui_labels"]["marian_mt"] == "Marian MT"
    assert d["segment_stats"]["llm_adaptation"]["avg_sec_per_segment"] > 1.4
    assert d["phase_status"]["marian_mt"] == "done"


def test_live_payload_phase_status():
    tracker = {
        "segment_count": 312,
        "translate_started_mono": 0.0,
        "phase_started_mono": 100.0,
        "current_subphase": "llm_adaptation",
        "frozen": {
            "marian_mt": {"sec": 18.2, "segments": 312},
        },
        "marian_segments_done": 312,
        "llm_segments_done": 40,
    }
    payload = _build_live_timing_payload(tracker, "llm_adaptation")
    assert payload["phase_status"]["marian_mt"] == "done"
    assert payload["phase_status"]["llm_adaptation"] == "active"
    assert payload["phase_status"]["post_processing"] == "pending"
    assert payload["ui_buckets"]["marian_mt"] == 18.2
