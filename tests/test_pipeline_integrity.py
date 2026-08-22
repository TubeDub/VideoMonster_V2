"""Unit tests — Pipeline Integrity Contract (Stage 3A.1, TZ §14)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from engines.pipeline_integrity import (
    ArchitectureGuard,
    ArtifactIntegrityGuard,
    PipelineAudioIdentityError,
    PipelineIntegrityCoordinator,
    PipelineValidationError,
    RuntimeIntegrityError,
    Segment,
    StageSnapshotGuard,
    StageTransaction,
    ensure_segment_ids,
    new_segment_id,
    validation_always_enabled,
)
from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError
from engines.pipeline_integrity.artifact_registry import sha256_file
from engines.pipeline_integrity.guards import PipelineValidator
from engines.pipeline_integrity.rollback import run_stage_atomic


def _seg_row(**kwargs) -> dict:
    base = {"segment_id": new_segment_id(), "index": 0, "text": "hello", "file": None}
    base.update(kwargs)
    return base


class TestImmutableSegmentModel:
    def test_segment_from_dict_roundtrip(self):
        row = _seg_row(file="a.mp3", start_ms=100, end_ms=2000)
        seg = Segment.from_dict(row)
        out = seg.to_dict()
        assert out["segment_id"] == row["segment_id"]
        assert out["text"] == "hello"
        assert out["file"] == "a.mp3"

    def test_evolve_produces_new_instance(self):
        seg = Segment.from_dict(_seg_row())
        seg2 = seg.evolve(text="changed")
        assert seg.text == "hello"
        assert seg2.text == "changed"
        assert seg.segment_id == seg2.segment_id


class TestArchitectureGuard:
    def test_missing_segment_id_is_repaired(self):
        rows = [{"index": 0, "text": "x"}]
        ArchitectureGuard.check(rows, stage="test")
        assert str(rows[0].get("segment_id") or "").strip()

    def test_duplicate_segment_id_is_repaired(self):
        sid = new_segment_id()
        rows = [_seg_row(segment_id=sid, index=0), _seg_row(segment_id=sid, index=1)]
        ArchitectureGuard.check(rows, stage="test")
        ids = [str(r["segment_id"]) for r in rows]
        assert all(ids)
        assert len(set(ids)) == 2


class TestRuntimeIntegrityGuard:
    def test_timing_map_shorter_raises(self):
        rows = [_seg_row(index=0)]
        with pytest.raises(RuntimeIntegrityError):
            from engines.pipeline_integrity.guards import RuntimeIntegrityGuard

            RuntimeIntegrityGuard.check(rows, [], stage="test")

    def test_invalid_merge_pointer_raises(self):
        sid = new_segment_id()
        rows = [
            _seg_row(segment_id=sid, index=0),
            _seg_row(index=1, merged_into_id="nonexistent"),
        ]
        with pytest.raises(RuntimeIntegrityError):
            from engines.pipeline_integrity.guards import RuntimeIntegrityGuard

            RuntimeIntegrityGuard.check(rows, [{"start": 0, "end": 1000}], stage="test")

    def test_slot_fit_regen_file_used_when_canonical_missing(self, tmp_path):
        """After slot_fit regen, integrity must check seg['file'], not stale tts_file_path."""
        from engines.pipeline_integrity.guards import RuntimeIntegrityGuard
        from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

        canonical = "6160133d_g0012.mp3"
        regen = "slot_12_fcaad529.mp3"
        (tmp_path / regen).write_bytes(b"mp3")
        row = _seg_row(
            index=12,
            file=regen,
            tts_file_path=canonical,
        )
        assert resolve_segment_audio_ref(row) == regen

        def resolve_audio(fname, *, task_info=None):
            return tmp_path / fname

        RuntimeIntegrityGuard.check(
            [row],
            [{"start": 0, "end": 5000}],
            stage="studio_handoff",
            require_tts=True,
            task_info={"task_id": "t1"},
            resolve_audio=resolve_audio,
        )


class TestStageSnapshotGuard:
    def test_disallowed_mutation_raises(self):
        before = [_seg_row(file=None)]
        after = copy.deepcopy(before)
        after[0]["text"] = "changed translation"
        with pytest.raises(Exception) as exc:
            StageSnapshotGuard.check(before, after, stage="tts")
        err = exc.value
        assert "text" in str(err)
        assert getattr(err, "field", "") == "text"
        assert getattr(err, "segment_id", "")
        assert getattr(err, "allowed_mutations", [])
        assert "tts_text" in getattr(err, "allowed_mutations", [])
        assert "plain_text" not in getattr(err, "allowed_mutations", [])

    def test_allowed_tts_file_mutation_passes(self):
        before = [_seg_row(file=None)]
        after = copy.deepcopy(before)
        after[0]["tts_file_path"] = "seg.mp3"
        after[0]["tts_text"] = "hello"
        after[0]["status"] = "generated"
        StageSnapshotGuard.check(before, after, stage="tts")

    def test_legacy_file_field_mutation_at_tts_allowed(self):
        before = [_seg_row(file=None)]
        after = copy.deepcopy(before)
        after[0]["file"] = "5b8fd005_seg0000.mp3"
        after[0]["tts_status"] = "generated"
        StageSnapshotGuard.check(before, after, stage="tts")
        assert StageSnapshotGuard.diff_violations(before, after, stage="tts") == []

    def test_plain_text_mutation_raises(self):
        before = [_seg_row(file=None, plain_text="immutable")]
        after = copy.deepcopy(before)
        after[0]["plain_text"] = "changed by tts"
        with pytest.raises(StageSnapshotIntegrityError) as exc:
            StageSnapshotGuard.check(before, after, stage="tts")
        assert exc.value.field == "plain_text"

    def test_rich_diagnostic_block(self):
        before = [_seg_row(file=None, text="old")]
        after = copy.deepcopy(before)
        after[0]["translation_text"] = "new"
        with pytest.raises(Exception) as exc:
            StageSnapshotGuard.check(before, after, stage="tts")
        block = exc.value.format_diagnostic_block()
        assert "field:" in block
        assert "translation_text" in block
        assert "previous_value:" in block
        assert "new_value:" in block
        assert "allowed_mutations:" in block
        assert "mutator_module:" in block
        assert "engines.tts" in block


class TestStageSnapshotGuardLifecycle:
    def test_skips_when_project_session_missing(self):
        rows = [_seg_row()]
        coord = PipelineIntegrityCoordinator(task_id="t1")
        coord.assign_segment_ids(rows)
        assert coord.initialize_guard_context(project_session=None, segments_data=rows) is False
        coord.begin_stage("tts", rows)
        after = copy.deepcopy(rows)
        after[0]["file"] = "a.mp3"
        coord.end_stage("tts", after)
        assert any(r.get("snapshot_guard") == "skipped" for r in coord.reports)

    def test_skips_when_no_segments(self, tmp_path: Path):
        from engines.dubbing_engine.project_session import ProjectSession

        session = ProjectSession("sess1", tmp_path, task_id="t1")
        coord = PipelineIntegrityCoordinator(task_id="t1")
        assert coord.initialize_guard_context(project_session=session, segments_data=[]) is False
        assert coord._guard_skip_reason == "no Segment objects"
        assert not coord.is_snapshot_guard_ready()

    def test_runs_after_bootstrap_snapshot(self, tmp_path: Path):
        from engines.dubbing_engine.project_session import ProjectSession

        session = ProjectSession("sess2", tmp_path, task_id="t2")
        rows = [_seg_row(file=None)]
        coord = PipelineIntegrityCoordinator(task_id="t2")
        coord.assign_segment_ids(rows)
        assert coord.initialize_guard_context(project_session=session, segments_data=rows) is True
        coord.begin_stage("tts", rows)
        bad = copy.deepcopy(rows)
        bad[0]["text"] = "mutated"
        # No task_info → default UI is Simple/basic: Stage 40 soft-continues.
        coord.end_stage("tts", bad)
        assert any(r.get("snapshot_guard") == "soft_continue" for r in coord.reports)


class TestArtifactIntegrityGuard:
    def test_no_audio_reuse_raises(self, tmp_path: Path):
        f = tmp_path / "shared.mp3"
        f.write_bytes(b"\xff\xfb" + b"\x00" * 32)
        guard = ArtifactIntegrityGuard()
        rows = [
            _seg_row(segment_id="a", file=f.name),
            _seg_row(segment_id="b", index=1, file=f.name),
        ]

        def resolve(name, task_info=None):
            return tmp_path / Path(str(name)).name

        guard.register_segments(rows[:1], resolve_path=resolve, task_info=None, stage="tts")
        with pytest.raises(PipelineAudioIdentityError):
            guard.register_segments(rows[1:], resolve_path=resolve, task_info=None, stage="tts")

    def test_sha256_registry(self, tmp_path: Path):
        f = tmp_path / "one.mp3"
        payload = b"\xff\xfb" + b"\xab" * 64
        f.write_bytes(payload)
        assert sha256_file(f) == sha256_file(f)
        assert len(sha256_file(f)) == 64


class TestPipelineValidator:
    def test_empty_tts_raises(self):
        rows = [_seg_row(file=None)]
        with pytest.raises((PipelineValidationError, RuntimeIntegrityError)):
            PipelineValidator.validate(rows, [{"start": 0, "end": 1000}], stage="handoff")

    def test_valid_project_passes(self, tmp_path: Path):
        f = tmp_path / "seg.mp3"
        f.write_bytes(b"\xff\xfb" + b"\x00" * 32)
        sid = new_segment_id()
        rows = [_seg_row(segment_id=sid, file=f.name)]

        def resolve(name, task_info=None):
            return tmp_path / Path(str(name)).name

        coord = PipelineIntegrityCoordinator(task_id="t1")
        coord.register_tts_artifacts(rows, resolve_path=resolve, task_info=None)
        result = coord.validate_pipeline(
            rows,
            [{"start": 0, "end": 2000}],
            stage="handoff",
            resolve_audio=resolve,
        )
        assert result["with_tts"] == 1

    def test_reissue_child_registers_at_handoff(self, tmp_path: Path):
        """studio_handoff: NEW id after reissue must sync into artifact registry."""
        f = tmp_path / "child.mp3"
        f.write_bytes(b"\xff\xfb" + b"\x00" * 32)
        old_sid = new_segment_id()
        new_sid = new_segment_id()
        # Parent was registered at TTS; child appears later with its own file.
        parent = _seg_row(segment_id=old_sid, file="parent.mp3")
        child = _seg_row(
            segment_id=new_sid,
            file=f.name,
            reissued_from=[old_sid],
        )
        parent_file = tmp_path / "parent.mp3"
        parent_file.write_bytes(b"\xff\xfb" + b"\x11" * 32)

        def resolve(name, task_info=None):
            return tmp_path / Path(str(name)).name

        coord = PipelineIntegrityCoordinator(task_id="t-reissue")
        coord.register_tts_artifacts([parent], resolve_path=resolve, task_info=None)
        # Simulate post-TTS reissue: only child remains active
        result = coord.validate_pipeline(
            [child],
            [{"start": 0, "end": 2000}],
            stage="studio_handoff",
            resolve_audio=resolve,
        )
        assert result["with_tts"] == 1
        assert new_sid in coord.artifact_guard.registry.records

    def test_reissue_transfers_file_from_archived_parent(self, tmp_path: Path):
        """File still bound to archived parent id → transfer to child."""
        f = tmp_path / "shared.mp3"
        f.write_bytes(b"\xff\xfb" + b"\x00" * 32)
        old_sid = new_segment_id()
        new_sid = new_segment_id()
        parent = _seg_row(segment_id=old_sid, file=f.name)
        child = _seg_row(
            segment_id=new_sid,
            file=f.name,
            reissued_from=[old_sid],
        )

        def resolve(name, task_info=None):
            return tmp_path / Path(str(name)).name

        coord = PipelineIntegrityCoordinator(task_id="t-xfer")
        coord.register_tts_artifacts([parent], resolve_path=resolve, task_info=None)
        result = coord.validate_pipeline(
            [child],
            [{"start": 0, "end": 2000}],
            stage="studio_handoff",
            resolve_audio=resolve,
        )
        assert result["with_tts"] == 1
        assert new_sid in coord.artifact_guard.registry.records
        assert old_sid not in coord.artifact_guard.registry.records
        assert coord.artifact_guard.registry.file_to_segment[f.name] == new_sid


class TestRollbackContract:
    def test_rollback_restores_segments_on_error(self):
        rows = [_seg_row(text="original")]

        def boom() -> None:
            raise PipelineValidationError("fail", stage="x")

        with pytest.raises(PipelineValidationError):
            run_stage_atomic("slot_fit", rows, None, boom)
        assert rows[0]["text"] == "original"


class TestValidationMandatory:
    def test_validation_cannot_be_disabled(self, monkeypatch):
        monkeypatch.setenv("VM_DEV_MODE", "1")
        monkeypatch.setenv("VM_SKIP_VALIDATION", "1")
        assert validation_always_enabled() is True


class TestCoordinatorBootstrap:
    def test_assign_segment_ids_deterministic_count(self):
        rows = [{"index": i, "text": f"t{i}", "file": None} for i in range(5)]
        coord = PipelineIntegrityCoordinator(task_id="abc")
        coord.assign_segment_ids(rows)
        ids = [r["segment_id"] for r in rows]
        assert len(ids) == len(set(ids))
