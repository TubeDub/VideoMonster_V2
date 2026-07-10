"""Unit tests for Stage 3A Conflict Resolver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.conflict_resolver import (
    MIN_GAP_MS,
    OVERLAP_TOLERANCE_MS,
    SegmentPlacement,
    apply_resolver_to_fitted,
    placements_from_fitted,
    resolve_conflicts,
    write_resolver_report,
)


def _seg(
    idx: int,
    start: int,
    duration: int,
    *,
    place: int | None = None,
    slot_end: int | None = None,
) -> SegmentPlacement:
    return SegmentPlacement(
        idx=idx,
        original_start_ms=start,
        slot_end_ms=slot_end or start + duration + 500,
        place_start_ms=place if place is not None else start,
        duration_ms=duration,
    )


def _overlap(a: SegmentPlacement, b: SegmentPlacement) -> int:
    return max(0, a.effective_end_ms - b.place_start_ms + MIN_GAP_MS)


class TestNoOverlapInvariant:
    def test_free_window_resolves_overlap(self):
        prev = _seg(0, 0, 1000, place=0)
        cur = _seg(1, 920, 800, place=920)
        result = resolve_conflicts([prev, cur], collect_traces=False)
        assert result.overlaps_resolved >= 1
        assert _overlap(prev, cur) <= OVERLAP_TOLERANCE_MS
        assert prev.strategy in ("free_window", "local_shift", "local_reflow", "safe_stretch")

    def test_no_domino_right_segment_unchanged(self):
        a = _seg(0, 0, 2000, place=0)
        b = _seg(1, 1500, 1000, place=1500)
        c = _seg(2, 3000, 500, place=3000)
        original_c_start = c.place_start_ms
        resolve_conflicts([a, b, c], collect_traces=False)
        assert c.place_start_ms == original_c_start


class TestDeterminism:
    def test_identical_results_on_repeat(self):
        segments = [
            _seg(0, 0, 1200, place=0),
            _seg(1, 1000, 900, place=1000),
            _seg(2, 2200, 600, place=2200),
        ]
        r1 = resolve_conflicts([s for s in segments], collect_traces=False)
        r2 = resolve_conflicts([s for s in segments], collect_traces=False)
        assert [s.place_start_ms for s in r1.segments] == [s.place_start_ms for s in r2.segments]
        assert [s.strategy for s in r1.segments] == [s.strategy for s in r2.segments]
        assert r1.intervention_map == r2.intervention_map


class TestStrategyPriority:
    def test_prefers_shift_before_stretch(self):
        prev = _seg(0, 0, 1000, place=0)
        cur = _seg(1, 950, 400, place=950)
        resolve_conflicts([prev, cur], collect_traces=False)
        assert cur.strategy in ("free_window", "local_shift", "gap_close", "lip_clamp", "none")
        assert cur.strategy != "safe_stretch"

    def test_overflow_when_impossible(self):
        prev = _seg(0, 0, 3000, place=0)
        cur = _seg(1, 500, 5000, place=500, slot_end=600)
        result = resolve_conflicts([prev, cur], collect_traces=False)
        assert prev.status == "overflow"
        assert result.strategy_counts["overflow"] >= 1


class TestLipTiming:
    def test_clamps_too_early_start(self):
        seg = _seg(0, 2000, 500, place=1800)
        resolve_conflicts([seg], collect_traces=False)
        assert seg.place_start_ms >= seg.original_start_ms - 120

    def test_local_shift_scores_lip_proximity(self):
        prev = _seg(0, 0, 800, place=0)
        cur = _seg(1, 700, 600, place=700)
        resolve_conflicts([prev, cur], collect_traces=False)
        assert abs(cur.place_start_ms - cur.original_start_ms) <= 250


class TestInterventionMap:
    def test_metrics_present(self):
        segments = [_seg(i, i * 1000, 800, place=i * 1000) for i in range(5)]
        result = resolve_conflicts(segments, collect_traces=False)
        m = result.intervention_map
        assert "intact_pct" in m
        assert "shift_only_pct" in m
        assert "stretch_only_pct" in m
        assert "overflow_pct" in m
        assert "timeline_drift_ms" in m
        assert m["total_segments"] == 5


class TestApplyToFitted:
    def test_updates_place_start_in_mix(self):
        fitted = [
            {"idx": 0, "place_start": 0, "original_start_ms": 0, "slot_end_ms": 2000, "fitted_ms": 1000},
            {"idx": 1, "place_start": 900, "original_start_ms": 900, "slot_end_ms": 3000, "fitted_ms": 800},
        ]
        mix = [("a.wav", 0, 1000), ("b.wav", 900, 800)]
        timing_map = [{"start": 0, "end": 2000}, {"start": 900, "end": 3000}]
        result = apply_resolver_to_fitted(fitted, mix, timing_map)
        assert mix[1][1] == fitted[1]["place_start"]
        assert result.overlaps_resolved >= 0


class TestDeveloperReport:
    def test_writes_json(self, tmp_path: Path):
        result = resolve_conflicts([_seg(0, 0, 500, place=0)], collect_traces=True)
        path = write_resolver_report(result, task_id="test-task", session_dir=tmp_path)
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["task_id"] == "test-task"
        assert "intervention_map" in data
        assert "profile" in data


class TestPlacementsFromFitted:
    def test_builds_from_rows(self):
        rows = [{"idx": 0, "place_start": 100, "fitted_ms": 500, "original_start_ms": 100}]
        segs = placements_from_fitted(rows, [{"start": 100, "end": 2000}])
        assert len(segs) == 1
        assert segs[0].place_start_ms == 100
        assert segs[0].duration_ms == 500
