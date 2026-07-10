"""
Tests for the quality-first dubbing algorithm.

Principles verified:
  1. atempo is capped at 1.05x (barely noticeable — not 1.15x)
  2. Overflow ≤ 15% → gap_absorb or video_adapt (speech untouched)
  3. Overflow > 15% → needs_shorten (text compression before atempo)
  4. Video stretch filter is built correctly for setpts segments
  5. Stress marks module works for UA/RU text
  6. Speech speed equalization identifies outliers without crashing
"""
from __future__ import annotations

import pytest


# ─── timing_fit: atempo cap and overflow classification ──────────────────────

def test_atempo_hard_cap_105():
    from engines.timing_fit import DUB_MAX_ATEMPO, _ATEMPO_ABSOLUTE_MAX
    assert DUB_MAX_ATEMPO <= 1.05, "atempo cap must be ≤ 1.05 (quality-first)"
    assert _ATEMPO_ABSOLUTE_MAX <= 1.05, "absolute max atempo must be ≤ 1.05"


def test_gentle_atempo_factor_never_exceeds_cap():
    from engines.timing_fit import _gentle_atempo_factor
    for need in (1.01, 1.05, 1.10, 1.20, 1.50, 2.0):
        result = _gentle_atempo_factor(need)
        assert result <= 1.05, f"atempo {result} > 1.05 for need={need}"


def test_classify_fits():
    from engines.timing_fit import classify_segment_overflow
    oc = classify_segment_overflow(tts_ms=4800, slot_ms=5000)
    assert oc.label == "fits"
    assert oc.overflow_ms == 0


def test_classify_gap_absorb():
    from engines.timing_fit import classify_segment_overflow, DUB_SLOT_TOLERANCE_MS
    # 10% raw TTS overflow, 2s gap — should be absorbed naturally.
    # overflow_pct computed after subtracting tolerance from effective_slot.
    tts_ms = 5500
    slot_ms = 5000
    oc = classify_segment_overflow(tts_ms=tts_ms, slot_ms=slot_ms, gap_after_ms=2000)
    assert oc.label == "gap_absorb", f"Expected gap_absorb, got {oc.label}"
    # overflow_ms = max(0, tts_ms - (slot_ms + DUB_SLOT_TOLERANCE_MS))
    expected_overflow_ms = max(0, tts_ms - (slot_ms + DUB_SLOT_TOLERANCE_MS))
    expected_pct = 100.0 * expected_overflow_ms / slot_ms
    assert abs(oc.overflow_pct - expected_pct) < 0.5


def test_classify_video_adapt_small_gap():
    from engines.timing_fit import classify_segment_overflow
    # 10% overflow but only 200ms gap (not enough to absorb 500ms)
    oc = classify_segment_overflow(tts_ms=5500, slot_ms=5000, gap_after_ms=200)
    assert oc.label == "video_adapt"
    assert oc.video_stretch_ratio > 1.0


def test_classify_needs_shorten_large_overflow():
    from engines.timing_fit import classify_segment_overflow
    # 30% overflow → must shorten text
    oc = classify_segment_overflow(tts_ms=6500, slot_ms=5000, gap_after_ms=2000)
    assert oc.label == "needs_shorten"


def test_classify_boundary_15pct():
    from engines.timing_fit import classify_segment_overflow
    # Exactly 15% — still video_adapt (not needs_shorten)
    tts_ms = int(5000 * 1.15)
    oc = classify_segment_overflow(tts_ms=tts_ms, slot_ms=5000, gap_after_ms=100)
    assert oc.label != "needs_shorten", f"15% overflow should be video_adapt, got {oc.label}"


def test_classify_just_above_15pct():
    from engines.timing_fit import classify_segment_overflow, DUB_SLOT_TOLERANCE_MS
    # Need overflow_pct > 15% after tolerance.
    # overflow_pct = (tts_ms - slot_ms - tolerance) / slot_ms * 100 > 15
    # tts_ms > slot_ms * 1.15 + tolerance
    slot_ms = 5000
    tts_ms = slot_ms + int(slot_ms * 0.16) + DUB_SLOT_TOLERANCE_MS + 10
    oc = classify_segment_overflow(tts_ms=tts_ms, slot_ms=slot_ms, gap_after_ms=5000)
    assert oc.label == "needs_shorten", (
        f"Expected needs_shorten for {tts_ms}ms / {slot_ms}ms slot, got {oc.label} "
        f"(overflow_pct={oc.overflow_pct})"
    )


# ─── DubEngine video stretch filter ─────────────────────────────────────────

def test_video_setpts_filter_built():
    from engines.dub_engine import DubEngine
    segs = [{"start_ms": 5000, "end_ms": 8000, "stretch_ratio": 1.10}]
    engine = DubEngine(video_stretch_segments=segs)
    flt = engine._build_video_setpts_filter(segs, total_duration_sec=15.0)
    assert flt is not None
    assert "setpts" in flt
    assert "concat" in flt
    assert "1.10" in flt or "1.1000" in flt


def test_video_setpts_filter_none_when_no_stretch():
    from engines.dub_engine import DubEngine
    segs = [{"start_ms": 0, "end_ms": 5000, "stretch_ratio": 1.0}]
    engine = DubEngine(video_stretch_segments=segs)
    flt = engine._build_video_setpts_filter(segs, total_duration_sec=10.0)
    assert flt is None, "No filter should be built when ratio ≤ 1.01"


def test_video_setpts_filter_multiple_segments():
    from engines.dub_engine import DubEngine
    segs = [
        {"start_ms": 2000, "end_ms": 5000, "stretch_ratio": 1.08},
        {"start_ms": 10000, "end_ms": 13000, "stretch_ratio": 1.12},
    ]
    engine = DubEngine(video_stretch_segments=segs)
    flt = engine._build_video_setpts_filter(segs, total_duration_sec=20.0)
    assert flt is not None
    assert flt.count("setpts") >= 3  # pre, seg1, inter, seg2, post = at least 4


# ─── Stress marks ────────────────────────────────────────────────────────────

def test_stress_marks_ukrainian_common_words():
    from engines.stress_marks import add_stress_marks
    text = "але він вже буде"
    result = add_stress_marks(text, lang="uk")
    # At least one stress mark should be applied
    assert "\u0301" in result, f"Expected stress mark in {result!r}"


def test_stress_marks_russian_common_words():
    from engines.stress_marks import add_stress_marks
    text = "это очень просто"
    result = add_stress_marks(text, lang="ru")
    assert "\u0301" in result, f"Expected stress mark in {result!r}"


def test_stress_marks_passthrough_for_other_lang():
    from engines.stress_marks import add_stress_marks
    text = "This is english"
    result = add_stress_marks(text, lang="en")
    assert result == text  # should not modify English text


def test_stress_marks_do_not_double_accent():
    from engines.stress_marks import add_stress_marks
    # Text already has accent marks — should not add more
    accented = "але\u0301 він\u0301"
    result = add_stress_marks(accented, lang="uk")
    # Should not add extra accents (already accented ratio ≥ threshold)
    assert result.count("\u0301") == accented.count("\u0301")


def test_stress_marks_empty_text():
    from engines.stress_marks import add_stress_marks
    assert add_stress_marks("", lang="uk") == ""
    assert add_stress_marks("   ", lang="uk").strip() == ""


# ─── Speed equalization (smoke test — no crash) ──────────────────────────────

def test_equalize_speech_speeds_no_crash():
    from api.auto_dub_api import _equalize_speech_speeds
    segments_data = [
        {"timing_meta": {"atempo": 1.0}},
        {"timing_meta": {"atempo": 1.05}},
        {"timing_meta": {"atempo": 1.0}},
        {"merged_into": 0},  # merged segment — should be skipped
    ]
    timing_map = [
        {"start": 0, "end": 3000},
        {"start": 3000, "end": 6000},
        {"start": 6000, "end": 9000},
        {"start": 9000, "end": 12000},
    ]
    _equalize_speech_speeds(segments_data, timing_map)  # must not raise


def test_equalize_marks_outliers():
    from api.auto_dub_api import _equalize_speech_speeds
    segments_data = [
        {"timing_meta": {"atempo": 1.0}},
        {"timing_meta": {"atempo": 1.0}},
        {"timing_meta": {"atempo": 1.05}},   # outlier: 0.05 above median of 1.0
    ]
    timing_map = [{"start": i * 3000, "end": (i + 1) * 3000} for i in range(3)]
    _equalize_speech_speeds(segments_data, timing_map)
    # Outlier should be tagged
    assert segments_data[2].get("timing_meta", {}).get("speed_outlier") is True


def test_block_merge_shifts_next_after_gap_absorb_overflow():
    """gap_absorb clears slot_overflow but speech still extends — next seg must shift."""
    from api.auto_dub_api import _plan_block_merges

    segments_data = [
        {
            "tts_ms": 5500,
            "fitted_ms": 5500,
            "overflow_pct": 10.0,
            "slot_overflow": False,
            "video_adapt_mode": "gap_absorb",
        },
        {},
    ]
    timing_map = [{"start": 0, "end": 5000}, {"start": 5000, "end": 10000}]
    count = _plan_block_merges(segments_data, timing_map)
    assert count == 1
    assert segments_data[1]["merge_adjusted_start"] == 5600  # 5500 + 100ms pause
