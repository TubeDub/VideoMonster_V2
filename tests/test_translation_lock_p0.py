"""Unit + architecture + regression tests — Freeze TZ P0 Translation Lock."""

from __future__ import annotations

import copy

import pytest

from engines.pipeline_integrity import (
    DUB_CONTRACT_VERSION,
    FIELD_OWNERS,
    LOCKED_TEXT_FIELDS,
    OWNER_FIELD_GROUPS,
    PipelineState,
    PipelineStateError,
    StageSnapshotGuard,
    TRANSLATION_CONTRACT_VERSION,
    TranslationLockError,
    advance_pipeline_state,
    assert_owner_may_write,
    assert_transition,
    get_pipeline_state,
    lock_segments,
    require_contract_versions,
    stamp_contract_versions,
)
from engines.pipeline_integrity.contract_versions import ContractVersionError
from engines.pipeline_integrity.translation_lock import (
    assert_segments_text_immutable,
    is_project_locked,
    is_segment_locked,
)
from engines.translation_validation import (
    apply_translated_text_to_segment,
    apply_translation_lock_after_validation,
)


def _seg(**kwargs):
    base = {
        "segment_id": kwargs.pop("segment_id", "seg-001"),
        "index": 0,
        "translated_text": "Привіт",
        "semantic_text": "Привіт",
        "grammar_text": "Привіт",
        "plain_text": "Привіт",
        "text": "Привіт",
        "start_ms": 0,
        "end_ms": 1000,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Unit: Translation Lock
# ---------------------------------------------------------------------------


class TestTranslationLock:
    def test_lock_marks_segments_and_stamps_versions(self):
        info = {"pipeline_state": "VALIDATED", "segments_data": [_seg()]}
        meta = lock_segments(info["segments_data"], info=info)
        assert info["translation_locked"] is True
        assert is_segment_locked(info["segments_data"][0])
        assert get_pipeline_state(info) == PipelineState.LOCKED
        assert meta["translation_contract_version"] == TRANSLATION_CONTRACT_VERSION
        assert meta["dub_contract_version"] == DUB_CONTRACT_VERSION
        assert "translated_text" in info["segments_data"][0]["translation_lock_snapshot"]

    def test_lock_from_translated_advances_via_validated(self):
        info = {"pipeline_state": "TRANSLATED", "segments_data": [_seg()]}
        lock_segments(info["segments_data"], info=info)
        assert get_pipeline_state(info) == PipelineState.LOCKED

    def test_lock_from_new_raises(self):
        info = {"pipeline_state": "NEW", "segments_data": [_seg()]}
        with pytest.raises(TranslationLockError):
            lock_segments(info["segments_data"], info=info)

    def test_mutating_locked_text_raises(self):
        seg = _seg(translation_locked=True)
        with pytest.raises(TranslationLockError):
            apply_translated_text_to_segment(seg, "Інший текст")
        assert seg["translated_text"] == "Привіт"

    def test_assert_segments_text_immutable_detects_change(self):
        before = [_seg(translation_locked=True)]
        after = copy.deepcopy(before)
        after[0]["translated_text"] = "Змінено"
        with pytest.raises(TranslationLockError) as exc:
            assert_segments_text_immutable(before, after, mutator="test")
        assert exc.value.field == "translated_text"

    def test_timing_fields_mutable_after_lock(self):
        before = [_seg(translation_locked=True, start_ms=0, end_ms=1000)]
        after = copy.deepcopy(before)
        from engines.scheduler import update_time

        update_time(after, "seg-001", start_ms=50, end_ms=1100, playback_rate=1.05)
        # No raise — timing via Scheduler
        assert_segments_text_immutable(before, after)
        StageSnapshotGuard.check(before, after, stage="scheduler")

    def test_stage_snapshot_guard_blocks_locked_text(self):
        before = [_seg(translation_locked=True)]
        after = copy.deepcopy(before)
        after[0]["grammar_text"] = "hack"
        with pytest.raises(TranslationLockError):
            StageSnapshotGuard.check(before, after, stage="tts")

    def test_apply_lock_after_validation_helper(self):
        info = {
            "pipeline_state": "TRANSLATED",
            "segments_data": [_seg(), _seg(segment_id="seg-002", index=1)],
        }
        meta = apply_translation_lock_after_validation(info)
        assert is_project_locked(info)
        assert meta["locked_segments"] == 2
        # Idempotent
        meta2 = apply_translation_lock_after_validation(info)
        assert get_pipeline_state(info) == PipelineState.LOCKED
        assert meta2.get("pipeline_state") == "LOCKED"


# ---------------------------------------------------------------------------
# Unit: Pipeline State Machine
# ---------------------------------------------------------------------------


class TestPipelineStateMachine:
    def test_forward_transitions_ok(self):
        info: dict = {}
        assert get_pipeline_state(info) == PipelineState.NEW
        advance_pipeline_state(info, PipelineState.TRANSCRIBED)
        advance_pipeline_state(info, PipelineState.TRANSLATED)
        advance_pipeline_state(info, PipelineState.VALIDATED)
        advance_pipeline_state(info, PipelineState.LOCKED)
        advance_pipeline_state(info, PipelineState.TTS_READY)
        advance_pipeline_state(info, PipelineState.SCHEDULED)
        advance_pipeline_state(info, PipelineState.MERGED)
        advance_pipeline_state(info, PipelineState.HANDOFF)
        advance_pipeline_state(info, PipelineState.EXPORTED)
        assert get_pipeline_state(info) == PipelineState.EXPORTED

    def test_rollback_locked_to_translated_forbidden(self):
        with pytest.raises(PipelineStateError) as exc:
            assert_transition(PipelineState.LOCKED, PipelineState.TRANSLATED)
        assert "rollback" in str(exc.value).lower() or "forbidden" in str(exc.value).lower()

    def test_skip_forward_edge_forbidden(self):
        with pytest.raises(PipelineStateError):
            assert_transition(PipelineState.NEW, PipelineState.LOCKED)

    def test_same_state_is_noop(self):
        info = {"pipeline_state": "LOCKED"}
        advance_pipeline_state(info, PipelineState.LOCKED)
        assert get_pipeline_state(info) == PipelineState.LOCKED

    def test_advance_rejects_rollback(self):
        info = {"pipeline_state": "LOCKED"}
        with pytest.raises(PipelineStateError):
            advance_pipeline_state(info, PipelineState.VALIDATED)


# ---------------------------------------------------------------------------
# Unit: Contract Versions
# ---------------------------------------------------------------------------


class TestContractVersions:
    def test_stamp_and_require(self):
        info: dict = {}
        stamp_contract_versions(info)
        assert info["translation_contract_version"] == 1
        assert info["dub_contract_version"] == 1
        require_contract_versions(info)

    def test_mismatch_raises(self):
        info = {"translation_contract_version": 99, "dub_contract_version": 1}
        with pytest.raises(ContractVersionError):
            stamp_contract_versions(info)

    def test_missing_raises(self):
        with pytest.raises(ContractVersionError):
            require_contract_versions({})


# ---------------------------------------------------------------------------
# Architecture: Single Owner
# ---------------------------------------------------------------------------


class TestSingleOwnerArchitecture:
    def test_field_owners_cover_tz_groups(self):
        assert "Whisper" in OWNER_FIELD_GROUPS
        assert "Translation Engine" in OWNER_FIELD_GROUPS
        assert "Scheduler" in OWNER_FIELD_GROUPS
        assert "TTS Engine" in OWNER_FIELD_GROUPS
        assert "Merge Engine" in OWNER_FIELD_GROUPS

    def test_locked_text_owned_by_translation_engine(self):
        for field in (
            "translated_text",
            "semantic_text",
            "grammar_text",
            "corrected_text",
            "rewritten_text",
        ):
            assert FIELD_OWNERS[field] == "Translation Engine"
            assert field in LOCKED_TEXT_FIELDS

    def test_timing_owned_by_scheduler(self):
        assert FIELD_OWNERS["start_time"] == "Scheduler"
        assert FIELD_OWNERS["end_time"] == "Scheduler"

    def test_assert_owner_may_write_rejects_wrong_owner(self):
        with pytest.raises(TranslationLockError):
            assert_owner_may_write("translated_text", "TTS Engine")
        assert_owner_may_write("translated_text", "Translation Engine")

    def test_tts_cannot_change_translated_text_when_locked(self):
        before = [_seg(translation_locked=True, translated_text="A")]
        after = copy.deepcopy(before)
        after[0]["translated_text"] = "B"
        with pytest.raises(TranslationLockError):
            StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")


# ---------------------------------------------------------------------------
# Regression: closed-loop does not rewrite locked text
# ---------------------------------------------------------------------------


class TestClosedLoopRespectsLock:
    def test_closed_loop_marks_overflow_instead_of_rewrite(self, tmp_path):
        from engines.closed_loop_timing import run_closed_loop_segment

        seg = _seg(
            translation_locked=True,
            text="Дуже довгий текст який не вміщується в слот часу",
            plain_text="Дуже довгий текст який не вміщується в слот часу",
            playback_duration=5000,
            first_tts_duration_ms=5000,
            file="dummy.mp3",
        )
        timing_map = [{"start": 0.0, "end": 1.0, "duration": 1.0}]

        def _fake_regen(*_a, **_k):
            raise AssertionError("regen must not be called when locked")

        budget = run_closed_loop_segment(
            seg,
            0,
            timing_map,
            source_hint="hello",
            target_lang="uk",
            src_lang="en",
            voice="test",
            work_dir=tmp_path,
            regen_fn=_fake_regen,
            resolve_path=lambda f: str(tmp_path / f),
        )
        assert budget.final_status == "overflow_locked"
        assert "translation_lock" in budget.rewrite_reason
        assert seg["text"].startswith("Дуже довгий")
        assert seg.get("overflow") is True


# ---------------------------------------------------------------------------
# Final v3.0 P0: Conflict Detector + locked_text
# ---------------------------------------------------------------------------


class TestConflictDetectorP0:
    def test_blocks_non_owner_timing_write(self):
        from engines.pipeline_integrity.conflict_detector import (
            ConflictDetectorError,
            check_field_write,
        )

        with pytest.raises(ConflictDetectorError):
            check_field_write(
                "start_ms",
                requested_by="TTS Engine",
                segment=_seg(translation_locked=True),
            )

    def test_scheduler_may_write_timing(self):
        from engines.pipeline_integrity.conflict_detector import check_field_write

        result = check_field_write(
            "start_ms",
            requested_by="Scheduler",
            segment=_seg(translation_locked=True),
        )
        assert result.ok

    def test_locked_text_blocks_translation_engine(self):
        from engines.pipeline_integrity.conflict_detector import check_field_write

        with pytest.raises(TranslationLockError):
            check_field_write(
                "translated_text",
                requested_by="Translation Engine",
                segment=_seg(translation_locked=True),
            )

    def test_lock_sets_locked_text(self):
        info = {"pipeline_state": "VALIDATED", "segments_data": [_seg(plain_text="Привіт <break/>")]}
        lock_segments(info["segments_data"], info=info)
        assert info["segments_data"][0]["locked_text"] == "Привіт"
        assert is_segment_locked(info["segments_data"][0])
