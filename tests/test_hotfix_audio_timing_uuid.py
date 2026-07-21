"""Hotfix: audio_timing may stamp RevisionManager / uuid_chain fields."""

from __future__ import annotations

from engines.pipeline_integrity.guards import StageSnapshotGuard
from engines.pipeline_integrity.segment import new_segment_id
from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage
from engines.pipeline_integrity.uuid_chain import ensure_project_uuids


def test_audio_timing_allows_adaptation_uuid():
    allowed = allowed_fields_for_stage("audio_timing")
    assert "adaptation_uuid" in allowed
    assert "source_segment_uuid" in allowed
    assert "tts_uuid" in allowed


def test_stage_snapshot_accepts_uuid_chain_stamp():
    sid = new_segment_id()
    before = [
        {
            "segment_id": sid,
            "index": 0,
            "plain_text": "ok",
            "file": "a.wav",
            "playback_duration": 1000,
        }
    ]
    after = [dict(before[0])]
    ensure_project_uuids(after)
    # Must not raise StageSnapshotIntegrityError
    StageSnapshotGuard.check(before, after, stage="audio_timing")
    assert after[0].get("adaptation_uuid")
    assert after[0].get("source_segment_uuid")
