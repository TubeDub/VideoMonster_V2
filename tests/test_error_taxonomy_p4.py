"""P4 error taxonomy + metrics + ADR presence + architecture extras."""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.pipeline_integrity.error_taxonomy import (
    TAXONOMY,
    DubMetrics,
    IdentityError,
    MergeError,
    SegmentOverflowError,
    StudioError,
    TimingError,
    TranslationError,
    TTSError,
    classify_exception,
)
from engines.pipeline_integrity.exceptions import ContractVersionError
from engines.pipeline_integrity import StageSnapshotGuard, TranslationLockError
from engines.scheduler.errors import SchedulerError

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"


def test_taxonomy_covers_tz_errors():
    required = {
        "TranslationError",
        "TimingError",
        "TTSError",
        "SchedulerError",
        "StudioError",
        "MergeError",
        "IdentityError",
        "OverflowError",
        "ContractVersionError",
    }
    assert required.issubset(set(TAXONOMY))


def test_classify_exception():
    assert classify_exception(TranslationError("x")) == "TranslationError"
    assert classify_exception(SchedulerError("x")) == "SchedulerError"
    assert classify_exception(ContractVersionError("x")) == "ContractVersionError"


def test_dub_metrics_roundtrip():
    m = DubMetrics(overlap_count=2, overflow_count=1, borrowed_time=40, stretch_percent=5.0)
    d = m.to_dict()
    assert d["overlap_count"] == 2
    assert d["borrowed_time"] == 40


def test_adr_files_exist():
    for i in range(1, 7):
        matches = list(ADR_DIR.glob(f"ADR-{i:03d}-*.md"))
        assert matches, f"missing ADR-{i:03d}"


def test_tts_cannot_change_translated_text_architecture():
    before = [{"segment_id": "s1", "translated_text": "A", "translation_locked": True}]
    after = [{"segment_id": "s1", "translated_text": "B", "translation_locked": True}]
    with pytest.raises(TranslationLockError):
        StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")


def test_merge_cannot_change_translated_text_architecture():
    before = [{"segment_id": "s1", "translated_text": "A", "translation_locked": True}]
    after = [{"segment_id": "s1", "translated_text": "B", "translation_locked": True}]
    with pytest.raises(TranslationLockError):
        StageSnapshotGuard.check(
            before, after, stage="studio_handoff", mutator_module="engines.dub_engine"
        )


def test_error_classes_instantiate():
    for cls in (
        TranslationError,
        TimingError,
        TTSError,
        StudioError,
        MergeError,
        IdentityError,
        SegmentOverflowError,
    ):
        exc = cls("msg")
        assert exc.code
