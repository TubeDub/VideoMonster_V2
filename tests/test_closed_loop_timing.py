"""Tests for Closed Loop Timing Engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from engines.closed_loop_timing import (
    TimingBudget,
    allow_block_merge,
    build_timing_budget,
    build_timing_report,
    compute_timing_score,
    run_closed_loop_segment,
    run_closed_loop_timing,
    validate_timeline,
)


def test_compute_timing_score_perfect_fit():
    assert compute_timing_score(slot_ms=5000, actual_ms=5000) == 100.0


def test_compute_timing_score_penalizes_overflow():
    score = compute_timing_score(slot_ms=5000, actual_ms=6000)
    assert score < 90
    assert score > 0


def test_build_timing_budget_overflow():
    seg = {"playback_duration": 5480, "file": "x.wav"}
    timing = [{"start": 0, "end": 5200}]
    budget = build_timing_budget(seg, 0, timing, actual_ms=5480)
    assert budget.slot_duration == 5200
    assert budget.overflow == 280
    assert budget.status == "overflow"
    assert budget.delta == 280


def test_build_timing_budget_ok_within_tolerance():
    seg = {"playback_duration": 5250, "file": "x.wav"}
    timing = [{"start": 0, "end": 5200}]
    budget = build_timing_budget(seg, 0, timing, actual_ms=5250)
    assert budget.status == "ok"


def test_allow_block_merge_default_off(monkeypatch):
    monkeypatch.delenv("VM_ALLOW_BLOCK_MERGE", raising=False)
    assert allow_block_merge() is False
    monkeypatch.setenv("VM_ALLOW_BLOCK_MERGE", "1")
    assert allow_block_merge() is True


def test_closed_loop_accepts_fit_without_rewrite(tmp_path):
    seg = {
        "file": str(tmp_path / "a.wav"),
        "playback_duration": 2000,
        "plain_text": "короткий текст",
    }
    timing = [{"start": 0, "end": 2100}]
    with patch("engines.closed_loop_timing.apply_dynamic_pause_engine") as pause:
        pause.return_value = {"applied": False, "pause_adjustments_ms": 0, "stages": []}
        budget = run_closed_loop_segment(
            seg,
            0,
            timing,
            source_hint="short text",
            target_lang="uk",
            src_lang="en",
            voice="x",
            work_dir=tmp_path,
            regen_fn=None,
        )
    assert budget.final_status == "ok"
    assert budget.rewrite_iterations == 0
    assert seg["timing_score"] >= 95


def test_closed_loop_rewrites_on_overflow(tmp_path):
    seg = {
        "file": "a.wav",
        "playback_duration": 6000,
        "plain_text": "дуже довгий текст який не вміщується",
        "first_tts_duration_ms": 6000,
    }
    timing = [{"start": 0, "end": 4000}]

    class Opt:
        changed = True
        text = "коротший текст"
        stopped_reason = "llm_shorten"

    calls = {"n": 0}

    def regen(text, **kwargs):
        calls["n"] += 1
        return "b.wav", 3900

    with patch("engines.closed_loop_timing.apply_dynamic_pause_engine") as pause, patch(
        "engines.semantic_optimizer.optimize_llm_rephrase_for_slot", return_value=Opt()
    ), patch("engines.closed_loop_timing.measure_actual_ms", side_effect=[6000, 3900, 3900]):
        pause.return_value = {"applied": False, "pause_adjustments_ms": 0, "stages": []}
        budget = run_closed_loop_segment(
            seg,
            0,
            timing,
            source_hint="very long text",
            target_lang="uk",
            src_lang="en",
            voice="x",
            work_dir=tmp_path,
            regen_fn=regen,
            max_iterations=3,
        )
    assert calls["n"] >= 1
    assert budget.rewrite_iterations >= 1
    assert seg["plain_text"] == "коротший текст"
    assert budget.final_status == "ok"


def test_closed_loop_independent_segments(tmp_path):
    """Overflow on seg0 must not mutate seg1 start."""
    segs = [
        {
            "file": "a.wav",
            "playback_duration": 2000,
            "plain_text": "ok",
        },
        {
            "file": "b.wav",
            "playback_duration": 2000,
            "plain_text": "ok2",
        },
    ]
    timing = [
        {"start": 0, "end": 2100},
        {"start": 2200, "end": 4300},
    ]
    with patch("engines.closed_loop_timing.apply_dynamic_pause_engine") as pause:
        pause.return_value = {"applied": False, "pause_adjustments_ms": 0, "stages": []}
        stats = run_closed_loop_timing(
            segs,
            timing,
            source_segments=["a", "b"],
            voice="x",
            target_lang="uk",
            src_lang="en",
            work_dir=tmp_path,
            regen_fn=None,
        )
    assert segs[1].get("merge_adjusted_start") is None
    assert stats["checked"] == 2
    assert stats["ok"] == 2


def test_validate_timeline_detects_speech_overlap():
    segs = [
        {"file": "a.wav", "playback_duration": 3000},
        {"file": "b.wav", "playback_duration": 1000},
    ]
    timing = [
        {"start": 0, "end": 2000},
        {"start": 2100, "end": 4000},
    ]
    with patch("engines.closed_loop_timing.measure_actual_ms", side_effect=[3000, 1000]):
        result = validate_timeline(segs, timing)
    assert result["ok"] is False
    assert result["speech_overlap"]
    assert 0 in result["problem_indices"]


def test_build_timing_report_fields():
    segs = [
        {
            "file": "a.wav",
            "playback_duration": 5000,
            "timing_budget": {
                "index": 0,
                "original_duration": 5200,
                "measured_duration": 5000,
                "slot_duration": 5100,
                "slot_start": 0,
                "slot_end": 5100,
                "delta": -100,
                "overflow": 0,
                "underflow": 100,
                "rewrite_iterations": 1,
                "pause_adjustments_ms": 50,
                "pause_stages": ["tail_trim:50"],
                "timing_score": 98.0,
                "final_status": "ok",
                "rewrite_reason": "duration_overflow:smart_compression",
                "status": "ok",
            },
            "timing_score": 98.0,
        }
    ]
    report = build_timing_report(segs, [{"start": 0, "end": 5100}])
    assert report["segment_count"] == 1
    row = report["segments"][0]
    assert row["tts_duration"] == 5000
    assert row["slot_duration"] == 5100
    assert row["rewrite_iterations"] == 1
    assert row["timing_score"] == 98.0
    assert report["segments_score_ge_95"] == 1
