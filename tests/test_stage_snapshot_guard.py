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


def test_tts_diag_8c9850_identity_bind_is_allowed():
    """Regression: diagnostic 8c9850ef — StageSnapshotIntegrityError at tts.

    Cold EN→UK after TubeDub TZ: IdentityGuard bind_after_tts filled
    identity_binding.audio_path / tts_bound / bound_at_stage, plus
    RevisionManager revision_text_hash / tts_meta / wav_segment_id.
    segment_id and text_hash were unchanged. The snapshot whitelist for
    tts had not listed those TTS-owned bind fields, so the job aborted
    after 20/32 synths (tts_uk/mykyta) with STAGE_SNAPSHOT_INTEGRITY.
    """
    from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage

    allowed = allowed_fields_for_stage("tts")
    for field in (
        "identity_binding",
        "tts_meta",
        "revision_text_hash",
        "wav_segment_id",
    ):
        assert field in allowed

    sid = "d67f009d933b4a29b629162ff4b23745"
    text_hash = "8794b107e30f1cd876d1b5e3"
    text_revision = "f3af3d390eaf424d838bdc6b2ae029c3"
    before = [
        {
            "segment_id": sid,
            "index": 0,
            "identity_binding": {
                "segment_id": sid,
                "text_hash": text_hash,
                "text_revision": text_revision,
                "audio_path": "",
                "bound_at_stage": "pre_tts",
                "tts_bound": False,
            },
            "revision_text_hash": None,
            "tts_meta": None,
            "wav_segment_id": None,
        }
    ]
    after = [
        {
            "segment_id": sid,
            "index": 0,
            "identity_binding": {
                "segment_id": sid,
                "text_hash": text_hash,
                "text_revision": text_revision,
                "audio_path": "0000.mp3",
                "bound_at_stage": "post_tts",
                "tts_bound": True,
            },
            "revision_text_hash": "0dd698cc3dcc9d33ff24d122",
            "tts_meta": {
                "segment_id": sid,
                "text_hash": text_hash,
                "source_segment_uuid": sid,
                "translation_uuid": "7aac86366ccbc8c24e094d4125759c62",
                "adaptation_uuid": text_revision,
                "tts_uuid": "cff91dc1218f4a2397a3c8222615f35c",
                "sidecar_path": r"C:\tmp\segs\0000.mp3.vm_rev.json",
            },
            "wav_segment_id": sid,
            "tts_backend": "tts_uk",
            "tts_voice": "mykyta",
            "tts_language": "uk",
            "file": r"C:\tmp\segs\0000.mp3",
            "tts_file_path": r"C:\tmp\segs\0000.mp3",
        }
    ]
    StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")
    assert StageSnapshotGuard.diff_violations(before, after, stage="tts") == []

    after_text = [{**after[0], "plain_text": "changed"}]
    with pytest.raises(StageSnapshotIntegrityError) as exc:
        StageSnapshotGuard.check(before, after_text, stage="tts")
    assert exc.value.field == "plain_text"


def _coord_tts_task(tmp_path, task_id: str, *, simple: bool):
    from engines.dub_task_state import init_auto_task
    from engines.dubbing_engine.project_session import ProjectSession
    from engines.pipeline_integrity.guards import PipelineIntegrityCoordinator
    from engines.pipeline_integrity.segment import new_segment_id

    info = {
        "simple_pipeline": simple,
        "happy_path": simple,
        "session_dir": str(tmp_path / "sess"),
    }
    if not simple:
        # Default UI mode is basic → is_simple_pipeline() is True. Main/Pro
        # must opt into advanced adaptation so bind vs text can be distinguished.
        info["user_mode"] = "pro"
        info["use_advanced_adaptation"] = True
        info["simple_pipeline"] = False
        info["happy_path"] = False
    init_auto_task(
        task_id,
        {"status": "running", "step": "tts", "info": info},
    )
    session = ProjectSession("sess40", tmp_path, task_id=task_id)
    sid = new_segment_id()
    rows = [
        {
            "segment_id": sid,
            "index": 0,
            "text": "ok",
            "plain_text": "ok",
            "identity_binding": {
                "segment_id": sid,
                "text_hash": "abc",
                "text_revision": "rev",
                "audio_path": "",
                "bound_at_stage": "pre_tts",
                "tts_bound": False,
            },
        }
    ]
    coord = PipelineIntegrityCoordinator(task_id=task_id)
    coord.assign_segment_ids(rows)
    assert coord.initialize_guard_context(project_session=session, segments_data=rows)
    coord.begin_stage("tts", rows)
    return coord, rows, sid


def test_nonsimple_tts_identity_bind_does_not_abort(tmp_path):
    """Diag 8c9850ef fields must not abort main/non-Simple after partial TTS."""
    import copy

    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError

    coord, rows, sid = _coord_tts_task(tmp_path, "stage40nonsimple-bind", simple=False)
    after = copy.deepcopy(rows)
    after[0].update(
        {
            "identity_binding": {
                "segment_id": sid,
                "text_hash": "abc",
                "text_revision": "rev",
                "audio_path": r"C:\tmp\segs\0000.mp3",
                "bound_at_stage": "post_tts",
                "tts_bound": True,
            },
            "final_tts_text": "Привіт",
            "tts_text": "Привіт",
            "tts_meta": {
                "segment_id": sid,
                "sidecar_path": r"C:\tmp\segs\0000.mp3.vm_rev.json",
            },
            "revision_text_hash": "0dd698cc3dcc9d33ff24d122",
            "wav_segment_id": sid,
            "assigned_voice": "mykyta",
            "file": r"C:\tmp\segs\0000.mp3",
            "tts_file_path": r"C:\tmp\segs\0000.mp3",
            # Nested bind keys may also surface top-level.
            "audio_path": r"C:\tmp\segs\0000.mp3",
            "tts_bound": True,
            "bound_at_stage": "post_tts",
        }
    )
    coord.end_stage("tts", after)
    assert not any(
        r.get("snapshot_guard") == "skipped" and "error" in str(r).lower()
        for r in coord.reports
    )
    assert any(r.get("status") == "ok" or r.get("snapshot_guard") == "soft_continue" for r in coord.reports)

    # Safety net: bind field not on the TTS whitelist still continues (not a text swap).
    coord2, rows2, _sid2 = _coord_tts_task(
        tmp_path, "stage40nonsimple-bind2", simple=False
    )
    after2 = copy.deepcopy(rows2)
    after2[0]["text_revision"] = "rev-bind-only"
    coord2.end_stage("tts", after2)
    assert any(r.get("snapshot_guard") == "soft_continue" for r in coord2.reports)
    with STATE_LOCK:
        info = AUTO_TASKS["stage40nonsimple-bind2"]["info"]
        assert info.get("snapshot_soft_continue") is True

    coord3, rows3, _sid3 = _coord_tts_task(
        tmp_path, "stage40nonsimple-text", simple=False
    )
    after3 = copy.deepcopy(rows3)
    after3[0]["plain_text"] = "changed source"
    with pytest.raises(StageSnapshotIntegrityError) as raised:
        coord3.end_stage("tts", after3)
    assert raised.value.field == "plain_text"


def test_snapshot_mismatch_identity_bind_classifier():
    from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError
    from engines.pipeline_integrity.guards import snapshot_mismatch_is_identity_bind_only

    bind_err = StageSnapshotIntegrityError(
        "disallowed mutation of 'identity_binding'",
        stage="tts",
        field="identity_binding",
        details={
            "violations": [
                {"field": "tts_meta"},
                {"field": "identity_binding.audio_path"},
                {"field": "wav_segment_id"},
            ]
        },
    )
    assert snapshot_mismatch_is_identity_bind_only(bind_err, stage="tts") is True

    text_err = StageSnapshotIntegrityError(
        "disallowed mutation of 'plain_text'",
        stage="tts",
        field="plain_text",
        details={"violations": [{"field": "identity_binding"}, {"field": "plain_text"}]},
    )
    assert snapshot_mismatch_is_identity_bind_only(text_err, stage="tts") is False
