"""Tests for Auto Recovery + Micro Validator (TZ #5)."""

from __future__ import annotations

import json
import time

from core.chunk_manager import ChunkManager, ChunkStatus, PipelineChunk
from core.micro_validator import MicroValidator, get_validator
from core.pipeline_engine import PipelineEngine, PipelineEngineConfig
from core.recovery_manager import (
    ParkingQueue,
    RecoveryAction,
    RecoveryManager,
    recovery_enabled,
)


def _chunk(segs=None, chunk_id=0):
    segs = segs or ["hello", "world"]
    return PipelineChunk(
        chunk_id=chunk_id,
        segment_indices=list(range(len(segs))),
        source_segments=segs,
        timing_map=[{"start": i * 1000, "end": (i + 1) * 1000} for i in range(len(segs))],
        payload={"segments": segs},
    )


# ── Micro Validator ──────────────────────────────────────────────────


def test_llm_output_rejects_boilerplate():
    v = MicroValidator()
    r = v.validate_llm_output("Here is the translation: привіт")
    assert not r.ok
    assert any("boilerplate" in i.reason for i in r.issues)


def test_llm_output_rejects_markdown():
    v = MicroValidator()
    r = v.validate_llm_output("```\nпривіт\n```")
    assert not r.ok
    assert any("markdown" in i.reason for i in r.issues)


def test_llm_output_rejects_empty():
    v = MicroValidator()
    assert not v.validate_llm_output("").ok
    assert not v.validate_llm_output("   ").ok


def test_llm_output_rejects_truncation():
    v = MicroValidator()
    r = v.validate_llm_output("Some text that ends...")
    assert not r.ok
    assert any("truncated" in i.reason for i in r.issues)


def test_llm_output_accepts_clean():
    v = MicroValidator()
    assert v.validate_llm_output("Чистий переклад без зайвого.").ok


def test_llm_json_validation():
    v = MicroValidator()
    assert v.validate_llm_output('{"key": "value"}', expect_json=True).ok
    assert not v.validate_llm_output("not json", expect_json=True).ok


def test_translator_segment_count_mismatch():
    v = MicroValidator()
    c = _chunk(["a", "b"])
    c.payload["segments"] = ["only one"]
    r = v.validate_stage("translator", c)
    assert not r.ok


def test_integrity_detects_missing_segments():
    v = MicroValidator()
    c1 = PipelineChunk(chunk_id=0, segment_indices=[0, 1], source_segments=["a", "b"],
                       timing_map=[{"start": 0, "end": 1}] * 2)
    r = v.verify_integrity([c1], expected_segment_count=3)
    assert not r.ok
    assert any("missing" in i.reason for i in r.issues)


# ── Recovery Manager ─────────────────────────────────────────────────


def test_dynamic_timeout_scales_with_chunk_size():
    rm = RecoveryManager("t1")
    small = rm.compute_timeout("translator", chunk_segment_count=1)
    large = rm.compute_timeout("translator", chunk_segment_count=10)
    assert large > small
    assert small >= 15.0


def test_decide_action_retries_line_first():
    rm = RecoveryManager("t1")
    from core.micro_validator import LineIssue, ValidationResult

    vr = ValidationResult(
        ok=False,
        issues=[LineIssue(1, "empty_response"), LineIssue(3, "truncated")],
    )
    action, lines = rm.decide_action("translator", 5, validation=vr)
    assert action == RecoveryAction.RETRY_LINE
    assert 1 in lines and 3 in lines


def test_decide_action_parks_after_exhausted_retries():
    rm = RecoveryManager("t1")
    rm._chunk_retries[(0, "translator")] = 99
    action, _ = rm.decide_action("translator", 0, error="timeout")
    assert action == RecoveryAction.PARK


def test_parking_queue_does_not_block():
    pq = ParkingQueue()
    c = _chunk(chunk_id=7)
    pq.park(c, reason="test")
    assert pq.depth() == 1
    released = pq.release_ready()
    assert len(released) == 1
    assert released[0].chunk_id == 7


def test_stall_detection():
    rm = RecoveryManager("t1")
    rm.track_start(0, "translator")
    # Simulate old activity.
    with rm._lock:
        rm._running["0:translator"].last_activity = time.monotonic() - 9999
    stalled = rm.check_stalls(stall_threshold_s=1.0)
    assert len(stalled) >= 1
    assert stalled[0].reason == "stall_timeout"


def test_recovery_logging(tmp_path):
    rm = RecoveryManager("log-test", app_dir=tmp_path)
    rm.register_failure("translator", 3, "test error", action=RecoveryAction.PARK)
    log_file = tmp_path / "logs" / "recovery.log"
    assert log_file.exists()
    line = log_file.read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["chunk_id"] == 3
    assert data["agent"] == "translator"


def test_recovery_statistics_saved(tmp_path):
    rm = RecoveryManager("stats-test", app_dir=tmp_path)
    rm.stats.total_errors = 5
    rm.stats.successful_recoveries = 3
    rm.save_statistics()
    stats_file = tmp_path / "logs" / "recovery_statistics.json"
    assert stats_file.exists()
    data = json.loads(stats_file.read_text(encoding="utf-8"))
    assert data["total_errors"] == 5


def test_backoff_increases():
    rm = RecoveryManager("t1")
    b1 = rm.backoff("translator", 1)
    b3 = rm.backoff("translator", 3)
    assert b3 > b1


# ── Pipeline integration ─────────────────────────────────────────────


def test_pipeline_parks_failed_chunk_continues():
    """One failing chunk must not stop others (§8)."""
    config = PipelineEngineConfig(
        project_id="park-test",
        source_segments=["a", "b", "c", "d"],
        timing_map=[{"start": i, "end": i + 1} for i in range(4)],
        stages=("cleaner",),
        skip_stages=(),
        chunk_size=1,
        app_dir=".",
    )

    call_count = {"n": 0}

    def _sometimes_fail(chunk):
        call_count["n"] += 1
        if chunk.chunk_id == 1:
            raise RuntimeError("simulated failure")
        chunk.payload["segments"] = list(chunk.source_segments)
        return chunk

    engine = PipelineEngine(config)
    engine.register_handler("cleaner", _sometimes_fail)
    result = engine.run()
    # Other chunks should still process even if one was parked.
    assert call_count["n"] >= 2


def test_recovery_enabled_flag():
    import os

    os.environ["VM_RECOVERY"] = "1"
    assert recovery_enabled() is True
    os.environ["VM_RECOVERY"] = "0"
    assert recovery_enabled() is False
    os.environ.pop("VM_RECOVERY", None)
