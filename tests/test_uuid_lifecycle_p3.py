"""P3 UUID chain + TTS lifecycle tests."""

from __future__ import annotations

import pytest

from engines.pipeline_integrity.exceptions import PipelineIdentityError
from engines.pipeline_integrity.tts_artifact_lifecycle import (
    TTSLifecycleError,
    TTSLifecycleState,
    advance_tts_lifecycle,
    assert_tts_mutable,
    get_tts_lifecycle,
)
from engines.pipeline_integrity.uuid_chain import (
    UUID_FIELDS,
    assert_uuids_unique,
    ensure_all_uuids,
    ensure_project_uuids,
)


def test_ensure_all_uuids_unique_fields():
    seg = {"segment_id": "abc", "translated_text": "Привіт"}
    ids = ensure_all_uuids(seg)
    for field in UUID_FIELDS:
        assert ids[field]
        assert seg[field] == ids[field]
    assert len(set(ids.values())) >= 4  # translation may share prefix but fields differ


def test_project_uuids_unique():
    rows = [
        {"segment_id": "a", "translated_text": "one"},
        {"segment_id": "b", "translated_text": "two"},
    ]
    meta = ensure_project_uuids(rows)
    assert meta["segments"] == 2
    assert_uuids_unique(rows)


def test_duplicate_segment_uuid_raises():
    rows = [
        {"segment_id": "x", "segment_uuid": "same", "translation_uuid": "t1",
         "tts_uuid": "a", "audio_uuid": "b", "merge_uuid": "c"},
        {"segment_id": "y", "segment_uuid": "same", "translation_uuid": "t2",
         "tts_uuid": "d", "audio_uuid": "e", "merge_uuid": "f"},
    ]
    with pytest.raises(PipelineIdentityError):
        assert_uuids_unique(rows)


def test_tts_lifecycle_forward_only():
    seg = {"segment_id": "s1"}
    assert get_tts_lifecycle(seg) == TTSLifecycleState.CREATED
    advance_tts_lifecycle(seg, TTSLifecycleState.QUEUED)
    advance_tts_lifecycle(seg, TTSLifecycleState.SYNTHESIZED)
    advance_tts_lifecycle(seg, TTSLifecycleState.VERIFIED)
    advance_tts_lifecycle(seg, TTSLifecycleState.SCHEDULED)
    advance_tts_lifecycle(seg, TTSLifecycleState.MERGED)
    advance_tts_lifecycle(seg, TTSLifecycleState.RELEASED)
    with pytest.raises(TTSLifecycleError):
        advance_tts_lifecycle(seg, TTSLifecycleState.QUEUED)
    with pytest.raises(TTSLifecycleError):
        assert_tts_mutable(seg, action="rewrite")
