"""P5 performance budget + micro-benchmarks."""

from __future__ import annotations

import random
import time

import pytest

from engines.perf_budgets import (
    ALIGNMENT_BUDGET_MS,
    MERGE_BUDGET_MS,
    SCHEDULER_BUDGET_MS,
    assert_within_budget,
    measure_budget,
)
from engines.scheduler import Scheduler


def _segments(n: int, *, seed: int = 1):
    rng = random.Random(seed)
    rows = []
    t = 0
    for i in range(n):
        dur = rng.randint(200, 800)
        rows.append(
            {
                "segment_id": f"b-{i:05d}",
                "index": i,
                "start_ms": t,
                "end_ms": t + dur,
                "translated_text": f"seg{i}",
                "playback_duration": dur,
            }
        )
        t += dur + rng.randint(0, 50)
    return rows


def test_budget_constants():
    assert SCHEDULER_BUDGET_MS == 20.0
    assert MERGE_BUDGET_MS == 30.0
    assert ALIGNMENT_BUDGET_MS == 50.0


def test_scheduler_hotpath_within_budget():
    rows = _segments(50)
    sched = Scheduler()
    t0 = time.perf_counter()
    for row in rows:
        sched.update_time(
            rows,
            row["segment_id"],
            start_ms=int(row["start_ms"]),
            end_ms=int(row["end_ms"]) + 1,
        )
    # Per-call budget: average should be well under 20ms
    elapsed = (time.perf_counter() - t0) * 1000.0
    per_call = elapsed / max(len(rows), 1)
    assert per_call <= SCHEDULER_BUDGET_MS


def test_measure_budget_context():
    with measure_budget("merge", enforce=False) as sample:
        time.sleep(0.001)
    assert sample.elapsed_ms >= 0.5
    assert sample.budget_ms == MERGE_BUDGET_MS


def test_assert_within_budget_raises():
    with pytest.raises(Exception):
        assert_within_budget("alignment", ALIGNMENT_BUDGET_MS + 10)


def test_benchmark_1000_sequential_scheduler():
    rows = _segments(1000, seed=42)
    sched = Scheduler()
    t0 = time.perf_counter()
    for row in rows:
        sched.request_time(rows, row["segment_id"], int(row["end_ms"] - row["start_ms"]))
    total_ms = (time.perf_counter() - t0) * 1000.0
    # Throughput sanity: 1000 updates should finish in a few seconds on CI
    assert total_ms < 15000


def test_benchmark_random_segments_fingerprint_stable():
    from engines.audio_timing_optimizer import optimize_audio_timing

    rows = _segments(200, seed=7)
    for r in rows:
        r["translation_locked"] = True
        r["playback_duration"] = int(r["end_ms"] - r["start_ms"]) + 50
    r1 = optimize_audio_timing(rows, settings={"seed": 7})
    rows2 = _segments(200, seed=7)
    for r in rows2:
        r["translation_locked"] = True
        r["playback_duration"] = int(r["end_ms"] - r["start_ms"]) + 50
    r2 = optimize_audio_timing(rows2, settings={"seed": 7})
    assert r1.fingerprint == r2.fingerprint
