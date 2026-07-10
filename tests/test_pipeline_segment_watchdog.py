"""Tests for pipeline segment watchdog."""

import threading
import time

from engines.pipeline_segment_watchdog import (
    SEGMENT_PROCESS_TIMEOUT_SEC,
    run_segment_bounded,
)


def test_run_segment_bounded_completes_fast():
    watch = run_segment_bounded(
        task_id="t1",
        phase="test",
        segment_index=0,
        fn=lambda: "ok",
        fallback=lambda: "fb",
        timeout_sec=5.0,
    )
    assert watch.value == "ok"
    assert not watch.timed_out
    assert watch.elapsed_sec < 1.0


def test_run_segment_bounded_times_out_and_uses_fallback():
    def slow():
        time.sleep(SEGMENT_PROCESS_TIMEOUT_SEC + 2.0)
        return "late"

    watch = run_segment_bounded(
        task_id="t2",
        phase="test",
        segment_index=3,
        fn=slow,
        fallback=lambda: "fallback",
        timeout_sec=0.5,
    )
    assert watch.value == "fallback"
    assert watch.timed_out
    assert "timeout" in watch.error


def test_run_segment_bounded_is_a_hard_wall_clock_bound():
    """P0 no-hang: the watchdog must NOT wait for the orphaned worker.

    Regression for the ThreadPoolExecutor bug where ``with ... as pool`` blocked
    on ``shutdown(wait=True)`` until the slow worker finished, defeating the
    timeout (a 30s watchdog observed taking 114s). The bounded call must return
    the fallback in ~timeout_sec even though the worker sleeps far longer.
    """
    started = threading.Event()

    def very_slow():
        started.set()
        time.sleep(30.0)  # simulates a slow/cold local-LLM call
        return "late"

    t0 = time.perf_counter()
    watch = run_segment_bounded(
        task_id="t3",
        phase="test",
        segment_index=1,
        fn=very_slow,
        fallback=lambda: "fallback",
        timeout_sec=0.5,
    )
    elapsed = time.perf_counter() - t0

    assert started.is_set()          # worker really started
    assert watch.value == "fallback"
    assert watch.timed_out
    # The whole call must return promptly — NOT wait ~30s for the worker.
    assert elapsed < 5.0, f"watchdog waited too long ({elapsed:.1f}s)"


def test_run_segment_bounded_surfaces_worker_errors():
    def boom():
        raise ValueError("kaboom")

    watch = run_segment_bounded(
        task_id="t4",
        phase="test",
        segment_index=2,
        fn=boom,
        fallback=lambda: "fallback",
        timeout_sec=5.0,
    )
    assert watch.value == "fallback"
    assert not watch.timed_out
    assert "kaboom" in watch.error


def test_translation_review_manual_hold_only_in_dev_mode(monkeypatch):
    from api.auto_dub_api import _translation_review_requires_manual_hold

    monkeypatch.delenv("VM_DEV_MODE", raising=False)
    monkeypatch.delenv("VM_ARCHITECT_MODE", raising=False)
    assert not _translation_review_requires_manual_hold()

    monkeypatch.setenv("VM_DEV_MODE", "1")
    assert _translation_review_requires_manual_hold()
