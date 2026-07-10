"""Tests — TTS stage must not mutate plain_text (read-only after Translation)."""

from __future__ import annotations

import copy

import pytest

from engines.pipeline_integrity import StageSnapshotGuard, new_segment_id
from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError
from engines.pipeline_integrity.tts_segment_fields import (
    apply_tts_synthesis_result,
    resolve_tts_input_text,
    sync_tts_legacy_fields,
)


def test_resolve_tts_input_prefers_group_plain_text():
    group = {
        "plain_text": "Добрий вечір.",
        "text": "<speak>ignored</speak>",
    }
    assert resolve_tts_input_text(group) == "Добрий вечір."


def test_apply_tts_synthesis_only_contract_fields():
    seg = {
        "segment_id": new_segment_id(),
        "plain_text": "original",
        "translation_text": "original",
        "text": "original",
    }
    apply_tts_synthesis_result(
        seg,
        tts_text="Добрий вечір.",
        tts_file_path="seg.mp3",
        playback_duration=1200,
        status="generated",
    )
    assert seg["tts_text"] == "Добрий вечір."
    assert seg["tts_file_path"] == "seg.mp3"
    assert seg["playback_duration"] == 1200
    assert seg["status"] == "generated"
    assert seg["plain_text"] == "original"
    assert seg["translation_text"] == "original"
    assert seg["text"] == "original"


def test_guard_rejects_plain_text_change_at_tts():
    before = [{"segment_id": "s1", "plain_text": "A", "tts_text": ""}]
    after = copy.deepcopy(before)
    after[0]["tts_text"] = "B"
    after[0]["plain_text"] = "B"
    with pytest.raises(StageSnapshotIntegrityError) as exc:
        StageSnapshotGuard.check(before, after, stage="tts")
    assert exc.value.field == "plain_text"


def test_sync_legacy_maps_for_slot_fit():
    seg = {"tts_file_path": "out.mp3", "playback_duration": 900, "status": "generated"}
    sync_tts_legacy_fields([seg])
    assert seg["file"] == "out.mp3"
    assert seg["tts_ms"] == 900
    assert seg["tts_status"] == "generated"


def test_resolve_segment_audio_ref_prefers_working_file():
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

    seg = {
        "tts_file_path": "6160133d_g0012.mp3",
        "file": "slot_12_fcaad529.mp3",
    }
    assert resolve_segment_audio_ref(seg) == "slot_12_fcaad529.mp3"


def test_resolve_segment_audio_ref_falls_back_to_tts_file_path():
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref

    seg = {"tts_file_path": "6160133d_g0012.mp3", "file": None}
    assert resolve_segment_audio_ref(seg) == "6160133d_g0012.mp3"
