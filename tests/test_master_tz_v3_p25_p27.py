"""MASTER TZ v3.0 P25–P27 — CoW, Segment Transaction, R/W Separation."""

from __future__ import annotations

import copy

import pytest

from engines.pipeline_integrity.cow_snapshot import CowStageContext, apply_cow_result
from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.pipeline_integrity.guards import StageSnapshotGuard
from engines.pipeline_integrity.rw_contract import (
    assert_write_allowed,
    declare_stage_contract,
)
from engines.pipeline_integrity.segment_transaction import SegmentTransaction
from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage


def test_slot_fit_forbids_top_level_error_field():
    before = [{"segment_id": "s1", "fitted_file": "a.wav"}]
    after = [{"segment_id": "s1", "fitted_file": "a.wav", "slot_fit_error": "timeout"}]
    violations = StageSnapshotGuard.diff_violations(before, after, stage="slot_fit")
    assert any(v["field"] == "slot_fit_error" for v in violations)


def test_slot_fit_allows_timing_meta_error():
    before = [{"segment_id": "s1", "timing_meta": {}}]
    after = [
        {
            "segment_id": "s1",
            "timing_meta": {"slot_fit_error": "timeout", "slot_fit_stretch_only": True},
        }
    ]
    StageSnapshotGuard.check(before, after, stage="slot_fit")


def test_audio_timing_stage_allows_overflow_manager():
    before = [{"segment_id": "s1", "start_ms": 0, "end_ms": 1000}]
    after = [
        {
            "segment_id": "s1",
            "start_ms": 0,
            "end_ms": 1000,
            "overflow": True,
            "overflow_manager": {"severity": "critical", "overflow_ms": 500},
        }
    ]
    StageSnapshotGuard.check(before, after, stage="audio_timing")


def test_cow_working_copy_is_independent():
    segs = [{"segment_id": "a", "text": "x"}]
    ctx = CowStageContext.begin("slot_fit", segs)
    ctx.working[0]["fitted_ms"] = 123
    assert "fitted_ms" not in segs[0]
    assert ctx.input_snapshot.segments[0].get("fitted_ms") is None


def test_segment_transaction_rollback_on_violation():
    segs = [{"segment_id": "a", "text": "LOCKED", "translation_locked": True}]

    def _bad(working):
        working[0]["text"] = "CHANGED"
        return None

    tx = SegmentTransaction(stage="slot_fit", mutator_module="test")
    with pytest.raises(Exception):
        tx.run(segs, _bad)
    assert segs[0]["text"] == "LOCKED"


def test_segment_transaction_commit_allowed_fields():
    segs = [{"segment_id": "a", "fitted_file": None}]

    def _ok(working):
        working[0]["fitted_file"] = "fit.wav"
        working[0]["fitted_ms"] = 1000
        return "done"

    tx = SegmentTransaction(stage="slot_fit", mutator_module="test")
    assert tx.run(segs, _ok) == "done"
    assert segs[0]["fitted_file"] == "fit.wav"


def test_p27_write_outside_contract_raises():
    with pytest.raises(ArchitectureViolation):
        assert_write_allowed("slot_fit", "translated_text")
    assert_write_allowed("slot_fit", "fitted_file")


def test_p27_declare_contract_lists_writes():
    c = declare_stage_contract("audio_timing")
    assert "overflow_manager" in c["writes"] or "overflow" in c["writes"]
    assert "translated_text" not in c["writes"]


def test_audio_timing_in_allowed_fields():
    fields = allowed_fields_for_stage("audio_timing")
    assert "overflow_manager" in fields
    assert "start_ms" in fields
