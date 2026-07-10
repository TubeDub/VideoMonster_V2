"""Tests — TTS file lifecycle helpers (slot_fit regen must not delete canonical artifacts)."""

from __future__ import annotations

from pathlib import Path

from engines.pipeline_integrity.tts_file_lifecycle import safe_unlink_replaced_segment_audio


def test_safe_unlink_preserves_canonical_tts_file(tmp_path):
    canonical = "6160133d_g0012.mp3"
    canonical_path = tmp_path / canonical
    canonical_path.write_bytes(b"original")

    seg = {
        "segment_id": "s12",
        "index": 12,
        "tts_file_path": canonical,
        "file": canonical,
    }

    removed = safe_unlink_replaced_segment_audio(
        tmp_path,
        seg,
        canonical,
        task_id="t1",
        stage="slot_fit",
    )

    assert removed is False
    assert canonical_path.is_file()


def test_safe_unlink_removes_superseded_slot_file(tmp_path):
    old = "slot_12_abc123.mp3"
    old_path = tmp_path / old
    old_path.write_bytes(b"old")

    seg = {
        "segment_id": "s12",
        "index": 12,
        "tts_file_path": "6160133d_g0012.mp3",
        "file": old,
    }

    removed = safe_unlink_replaced_segment_audio(
        tmp_path,
        seg,
        old,
        task_id="t1",
        stage="slot_fit",
    )

    assert removed is True
    assert not old_path.is_file()
