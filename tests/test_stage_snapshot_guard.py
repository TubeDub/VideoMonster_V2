"""Tests for StageSnapshotIntegrityError rich diagnostics."""

from __future__ import annotations

import pytest

from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline
from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError
from engines.pipeline_integrity.guards import StageSnapshotGuard
from engines.pipeline_integrity.openddf_diagnostics import enrich_stage_snapshot_error


def test_stage_snapshot_error_user_reason_not_generic():
    err = StageSnapshotIntegrityError(
        "segment x: disallowed mutation of 'translation_text' at stage 'tts'",
        stage="tts",
        segment_id="abc123",
        field="translation_text",
        old_value="old text",
        new_value="new text",
        allowed_mutations=["file", "tts_text"],
        mutator_module="engines.tts",
    )
    reason = err.format_user_reason()
    assert "translation_text" in reason
    assert "abc123" in reason
    assert "Критическая ошибка" not in reason


def test_fail_pipeline_uses_openddf_block(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task

    task_id = "ssie-1"
    init_auto_task(
        task_id,
        {"status": "running", "step": "tts", "info": {"session_dir": str(tmp_path / "sess")}},
    )
    before = [{"segment_id": "s1", "prosody": {"rate": "0%"}}]
    after = [{"segment_id": "s1", "prosody": {"rate": "+10%"}}]
    violations = StageSnapshotGuard.diff_violations(before, after, stage="tts")
    first = violations[0]
    err = StageSnapshotIntegrityError(
        first["message"],
        stage="tts",
        segment_id="s1",
        field="prosody",
        old_value={"rate": "0%"},
        new_value={"rate": "+10%"},
        allowed_mutations=list(first.get("allowed_mutations") or []),
        mutator_module="engines.tts",
        details={"violations": violations},
    )
    enrich_stage_snapshot_error(
        err,
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
        fail_pipeline(task_id, err.format_user_reason(), stage="tts", exc=err, error_code="STAGE_SNAPSHOT_INTEGRITY")

    with STATE_LOCK:
        task = AUTO_TASKS[task_id]
        assert task["status"] == "error"
        pe = task["info"]["pipeline_error"]
        assert pe["error_type"] == "StageSnapshotIntegrityError"
        assert pe["error_code"] == "STAGE_SNAPSHOT_INTEGRITY"
        assert "prosody" in pe["reason"] or "prosody" in pe.get("reason_short", "")
        detail = task["info"]["last_pipeline_diagnostic"]
        assert "Field:" in detail
        assert "prosody" in detail
        assert "Recovery Hint:" in detail
        assert task["info"]["pipeline_error_developer"]["traceability"]


def test_guard_diff_returns_structured_violations():
    before = [{"segment_id": "s1", "index": 0, "text": "a", "file": None}]
    after = [{"segment_id": "s1", "index": 0, "text": "b", "file": None}]
    violations = StageSnapshotGuard.diff_violations(before, after, stage="tts")
    assert violations
    assert violations[0]["field"] == "text"
    assert violations[0]["old_value"] == "a"
    assert violations[0]["new_value"] == "b"


def test_slot_fit_cache_key_in_timing_meta_is_allowed():
    """slot_fit_key must live inside timing_meta, not as a top-level segment field."""
    before = [{"segment_id": "s1", "timing_meta": {"strategy": "none"}}]
    after = [{"segment_id": "s1", "timing_meta": {"strategy": "none", "slot_fit_key": "abc123"}}]
    StageSnapshotGuard.check(before, after, stage="slot_fit", mutator_module="api.auto_dub_api")


def test_slot_fit_top_level_cache_key_is_forbidden():
    before = [{"segment_id": "s1", "slot_fit_key": None}]
    after = [{"segment_id": "s1", "slot_fit_key": "abc123"}]
    with pytest.raises(StageSnapshotIntegrityError) as exc:
        StageSnapshotGuard.check(before, after, stage="slot_fit", mutator_module="api.auto_dub_api")
    assert exc.value.field == "slot_fit_key"


def test_slot_fit_does_not_mutate_text_or_tts_ms():
    before = [{
        "segment_id": "s1",
        "text": "Original translation",
        "tts_ms": 13392,
        "timing_meta": {},
    }]
    after = [{
        "segment_id": "s1",
        "text": "Original translation",
        "tts_ms": 13392,
        "fitted_file": "slot_0_fit.wav",
        "fitted_ms": 8496,
        "timing_meta": {
            "slot_fit_tts_ms": 8496,
            "slot_fit_text": "Shorter",
            "slot_fit_compressed": True,
            "slot_fit_key": "abc123",
        },
    }]
    StageSnapshotGuard.check(before, after, stage="slot_fit", mutator_module="api.auto_dub_api")


def test_slot_fit_text_mutation_is_forbidden():
    before = [{"segment_id": "s1", "text": "Original"}]
    after = [{"segment_id": "s1", "text": "Changed"}]
    with pytest.raises(StageSnapshotIntegrityError) as exc:
        StageSnapshotGuard.check(before, after, stage="slot_fit", mutator_module="api.auto_dub_api")
    assert exc.value.field == "text"


def test_slot_fit_tts_ms_mutation_is_forbidden():
    before = [{"segment_id": "s1", "tts_ms": 1000}]
    after = [{"segment_id": "s1", "tts_ms": 900}]
    with pytest.raises(StageSnapshotIntegrityError) as exc:
        StageSnapshotGuard.check(before, after, stage="slot_fit", mutator_module="api.auto_dub_api")
    assert exc.value.field == "tts_ms"


def test_tts_stage24_identity_stamps_are_allowed():
    """Regression: diagnostic 929afb54 — StageSnapshotIntegrityError on cyrillic_ratio/file/tts_language."""
    from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage
    from engines.pipeline_integrity.tts_segment_fields import TTS_ALLOWED_MUTATIONS

    allowed = allowed_fields_for_stage("tts")
    for field in ("cyrillic_ratio", "file", "resolved_path", "tts_language"):
        assert field in allowed
    assert TTS_ALLOWED_MUTATIONS <= allowed

    before = [{"segment_id": "f931312d8ed94c3d9ed9b50d52e03837", "index": 0}]
    after = [
        {
            "segment_id": "f931312d8ed94c3d9ed9b50d52e03837",
            "index": 0,
            "cyrillic_ratio": 1.0,
            "tts_language": "uk",
            "tts_backend": "tts_uk",
            "tts_voice": "mykyta",
            "file": r"C:\tmp\seg.mp3",
            "resolved_path": r"C:\tmp\seg.mp3",
            "tts_file_path": r"C:\tmp\seg.mp3",
            "tts_ms": 500,
            "playback_duration": 500,
        }
    ]
    StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")
    assert StageSnapshotGuard.diff_violations(before, after, stage="tts") == []


def test_tts_stage25_uk_hard_lock_voice_override_is_allowed():
    """Regression: diagnostic 0233d766 — StageSnapshotIntegrityError on 'voice' at 'tts'.

    Stage 25 §1 requires TTS stage to override the per-speaker `voice` from
    Piper style `uk_UA-*-high` to the canonical tts_uk short id (mykyta /
    tetiana / lada) or the safe Edge uk-UA-*Neural fallback when tts_uk is
    unavailable. This is an intentional architectural mutation, not a bug.
    """
    from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage

    allowed = allowed_fields_for_stage("tts")
    assert "voice" in allowed
    assert "voice_override_reason" in allowed

    before = [
        {
            "segment_id": "48dd35225dae44f79c8aace44263ee4a",
            "index": 0,
            "voice": "uk_UA-mykyta-high",
        }
    ]
    after = [
        {
            "segment_id": "48dd35225dae44f79c8aace44263ee4a",
            "index": 0,
            "voice": "mykyta",
            "voice_override_reason": "uk_hard_lock:uk_UA-mykyta-high->mykyta@tts_uk",
            "tts_backend": "tts_uk",
            "tts_engine": "tts_uk",
            "tts_voice": "mykyta",
            "tts_language": "uk",
        }
    ]
    StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")
    assert StageSnapshotGuard.diff_violations(before, after, stage="tts") == []
