"""P16.1 — Long-run / stress harness (fast mode for CI, full mode for labs)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from engines.audio_timing_optimizer import optimize_audio_timing
from engines.production_hardening.concurrency import run_concurrency_harness
from engines.production_hardening.resource_manager import (
    assert_no_resource_leak,
    take_resource_snapshot,
)
from engines.scheduler import Scheduler


@dataclass
class LongRunResult:
    ok: bool
    iterations: int
    elapsed_sec: float
    leak_issues: list[str]
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "iterations": self.iterations,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "leak_issues": self.leak_issues,
            "detail": self.detail,
        }


def _synthetic_batch(n: int, seed: int) -> list[dict[str, Any]]:
    rows = []
    t = 0
    for i in range(n):
        dur = 400 + (i * 17 + seed) % 400
        rows.append(
            {
                "segment_id": f"lr-{seed}-{i:05d}",
                "index": i,
                "translated_text": f"line-{i}",
                "text": f"line-{i}",
                "start_ms": t,
                "end_ms": t + dur,
                "slot_ms": dur,
                "playback_duration": dur + (i % 50),
                "translation_locked": True,
            }
        )
        t += dur + 20
    return rows


def run_long_run(
    *,
    duration_sec: float = 5.0,
    segments_per_iter: int = 50,
    projects_parallel: int = 4,
) -> LongRunResult:
    """
    Continuous processing loop until duration_sec elapsed.

    CI default: a few seconds. Lab: pass 8*3600 / 24*3600.
    """
    t0 = time.time()
    before = take_resource_snapshot()
    iterations = 0
    sched = Scheduler()
    while time.time() - t0 < duration_sec:
        rows = _synthetic_batch(segments_per_iter, seed=iterations)
        for row in rows:
            sched.update_time(
                rows,
                row["segment_id"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
            )
        optimize_audio_timing(rows, settings={"iter": iterations})
        if iterations % 3 == 0:
            run_concurrency_harness(
                projects=projects_parallel,
                segments_per_project=10,
                workers=min(4, projects_parallel),
            )
        iterations += 1
    after = take_resource_snapshot()
    leaks = assert_no_resource_leak(before, after)
    return LongRunResult(
        ok=not leaks and iterations > 0,
        iterations=iterations,
        elapsed_sec=time.time() - t0,
        leak_issues=leaks,
        detail=f"rss={before.rss_mb}->{after.rss_mb} threads={before.threads}->{after.threads}",
    )
