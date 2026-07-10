"""OpenDDF Developer Diagnostics v1.3 — diagnostic engine only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline
from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError
from engines.pipeline_integrity.guards import StageSnapshotGuard
from engines.pipeline_integrity.openddf_diagnostics import (
    build_dependency_pipeline,
    build_developer_payload,
    build_mutation_policy_context,
    build_release_payload,
    build_structured_diff,
    enrich_stage_snapshot_error,
    format_developer_block,
    guard_check_with_diagnostics,
    release_summary_from_exc,
)


def _slot_fit_exc() -> StageSnapshotIntegrityError:
    before = [{"segment_id": "s1", "slot_fit_key": None, "start_time": 0.0}]
    after = [{"segment_id": "s1", "slot_fit_key": "voice_slot_17", "start_time": 0.0}]
    violations = StageSnapshotGuard.diff_violations(before, after, stage="slot_fit")
    first = violations[0]
    return StageSnapshotIntegrityError(
        first["message"],
        stage="slot_fit",
        segment_id="s1",
        field="slot_fit_key",
        old_value=None,
        new_value="voice_slot_17",
        allowed_mutations=list(first.get("allowed_mutations") or []),
        mutator_module="engines.slot_fit",
        details={"violations": violations},
    )


def test_build_structured_diff_from_violations():
    exc = _slot_fit_exc()
    violations = exc.details["violations"]
    diffs = build_structured_diff([], [], violations)
    assert len(diffs) == 1
    assert diffs[0]["field"] == "slot_fit_key"
    assert diffs[0]["structured_diff"]["old"] == "null"
    assert diffs[0]["structured_diff"]["new"] == "voice_slot_17"


def test_mutation_policy_context_for_slot_fit_key():
    policy = build_mutation_policy_context(
        stage="slot_fit",
        field="slot_fit_key",
        allowed_mutations=["start_time", "end_time"],
    )
    assert "slot_fit_key" in policy["forbidden"]
    assert "start_time" in policy["allowed"]
    assert policy["reason"]


def test_dependency_pipeline_marks_failed_stage():
    path = build_dependency_pipeline("slot_fit")
    assert path[0] == "Segment"
    assert "slot_fit" in path
    assert path[-1] == "FAILED"


def test_enrich_writes_snapshot_artifacts(tmp_path):
    before = [{"segment_id": "s1", "slot_fit_key": None}]
    after = [{"segment_id": "s1", "slot_fit_key": "voice_slot_17"}]
    exc = _slot_fit_exc()
    enrich_stage_snapshot_error(
        exc,
        before=before,
        after=after,
        task_id="task-openddf",
        output_dir=tmp_path,
    )
    openddf = exc.details["openddf"]
    assert openddf["mode"] == "passive"
    assert openddf["developer"]["mode"] == "passive"
    assert openddf["release"]["error_code"] == "STAGE_SNAPSHOT_INTEGRITY"
    arts = openddf["artifacts"]
    for key in ("snapshot_before", "snapshot_after", "snapshot_diff", "report", "pipeline_log", "stacktrace", "environment"):
        p = Path(arts[key])
        assert p.is_file(), key
        if key.endswith(".json"):
            json.loads(p.read_text(encoding="utf-8"))


def test_developer_block_required_fields():
    exc = _slot_fit_exc()
    payload = build_developer_payload(
        exc,
        before=[{"segment_id": "s1", "slot_fit_key": None}],
        after=[{"segment_id": "s1", "slot_fit_key": "voice_slot_17"}],
        task_id="t1",
    )
    block = format_developer_block(payload)
    for label in (
        "Field:",
        "Old Value:",
        "New Value:",
        "Module:",
        "Function:",
        "File:",
        "Line:",
        "Allowed Mutations:",
        "Recovery Hint:",
    ):
        assert label in block
    assert "slot_fit_key" in block
    assert "None" in block
    assert "voice_slot_17" in block
    assert "Allowed Mutations" in block


def test_developer_block_contains_recovery_hint_text():
    exc = _slot_fit_exc()
    payload = build_developer_payload(
        exc,
        before=[{"segment_id": "s1", "slot_fit_key": None}],
        after=[{"segment_id": "s1", "slot_fit_key": "voice_slot_17"}],
        task_id="t1",
    )
    block = format_developer_block(payload)
    assert "slot_fit_key" in block
    assert "Recovery Hint:" in block
    assert "Allowed Mutations" in block


def test_guard_check_with_diagnostics_enriches(tmp_path):
    before = [{"segment_id": "s1", "plain_text": "a", "file": None}]
    after = [{"segment_id": "s1", "plain_text": "b", "file": None}]
    with pytest.raises(StageSnapshotIntegrityError) as raised:
        guard_check_with_diagnostics(
            before,
            after,
            stage="tts",
            mutator_module="engines.tts",
            task_id="guard-diag",
            output_dir=tmp_path,
        )
    exc = raised.value
    assert "openddf" in exc.details
    assert exc.details["openddf"]["developer"]["snapshot_diff"]["field"] == "plain_text"


def test_release_payload_hides_technical_details():
    exc = _slot_fit_exc()
    release = build_release_payload(exc)
    assert release["error_code"] == "STAGE_SNAPSHOT_INTEGRITY"
    assert "slot_fit_key" not in release["reason_short"]
    assert release["reason"]


def test_fail_pipeline_stores_openddf_payloads(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task

    task_id = "openddf-fail"
    init_auto_task(
        task_id,
        {"status": "running", "step": "slot_fit", "info": {"session_dir": str(tmp_path / "sess")}},
    )
    before = [{"segment_id": "s1", "slot_fit_key": None}]
    after = [{"segment_id": "s1", "slot_fit_key": "voice_slot_17"}]
    exc = _slot_fit_exc()
    enrich_stage_snapshot_error(
        exc,
        before=before,
        after=after,
        task_id=task_id,
        output_dir=tmp_path,
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "engines.dubbing_engine.pipeline_failure_diag.build_project_session_snapshot",
            lambda _tid: {},
        )
        mp.setattr("api.studio_api._save_session", lambda _s: None)
        fail_pipeline(
            task_id,
            exc.format_user_reason(),
            stage="slot_fit",
            exc=exc,
            error_code="STAGE_SNAPSHOT_INTEGRITY",
        )

    with STATE_LOCK:
        task = AUTO_TASKS[task_id]
        pe = task["info"]["pipeline_error"]
        assert pe["error_code"] == "STAGE_SNAPSHOT_INTEGRITY"
        assert pe["error_type"] == "StageSnapshotIntegrityError"
        dev = task["info"]["pipeline_error_developer"]
        assert dev["snapshot_diff"]["field"] == "slot_fit_key"
        assert task["info"]["openddf_artifacts"]["snapshot_before"]
        detail = task["info"]["last_pipeline_diagnostic"]
        assert "Field:" in detail
        assert "slot_fit_key" in detail
        assert "Recovery Hint:" in detail
        assert release_summary_from_exc(exc)["reason_short"] == pe["reason_short"]
