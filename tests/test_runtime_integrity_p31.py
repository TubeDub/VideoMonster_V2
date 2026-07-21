"""P3.1 Runtime Integrity / TTS Lifecycle / Studio Handoff tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.pipeline_integrity.cleanup_manager import CleanupManager
from engines.pipeline_integrity.error_taxonomy import HandoffViolation
from engines.pipeline_integrity.path_validation import validate_wav_path
from engines.pipeline_integrity.runtime_recovery import recover_missing_audio
from engines.pipeline_integrity.runtime_registry import RuntimeRegistry
from engines.pipeline_integrity.runtime_validator import (
    enforce_runtime,
    validate_runtime,
    write_diagnostic_zip,
)
from engines.pipeline_integrity.tts_artifact_lifecycle import (
    TTSLifecycleError,
    TTSLifecycleState,
    advance_tts_lifecycle,
    can_cleanup_wav,
    get_tts_lifecycle,
)
from engines.pipeline_integrity.uuid_chain import ensure_all_uuids
from engines.pipeline_integrity.wav_ownership import (
    CleanupViolationError,
    WavOwner,
    assert_cleanup_allowed,
    stamp_wav_owner,
)
from engines.pipeline_integrity.exceptions import RuntimeIntegrityError


def _wav(path: Path, payload: bytes = b"RIFF....WAVEfmt ") -> Path:
    # Minimal RIFF/WAVE header-ish payload (>=12 bytes with RIFF....WAVE)
    data = b"RIFF" + (100).to_bytes(4, "little") + b"WAVE" + payload
    path.write_bytes(data)
    return path


def _seg(tmp: Path, name: str = "a.wav") -> dict:
    wav = _wav(tmp / name)
    seg = {
        "segment_id": "s1",
        "translated_text": "hello",
        "start_ms": 0,
        "end_ms": 1000,
        "slot_ms": 1000,
        "file": wav.name,
        "tts_file_path": wav.name,
        "translation_locked": True,
    }
    ensure_all_uuids(seg)
    return seg


def test_lifecycle_full_path_no_rollback():
    seg = {"segment_id": "x"}
    for state in (
        TTSLifecycleState.QUEUED,
        TTSLifecycleState.SYNTHESIZING,
        TTSLifecycleState.SYNTHESIZED,
        TTSLifecycleState.VERIFIED,
        TTSLifecycleState.STORED,
        TTSLifecycleState.SCHEDULED,
        TTSLifecycleState.MERGED,
        TTSLifecycleState.HANDOFF_READY,
        TTSLifecycleState.EXPORTED,
        TTSLifecycleState.RELEASED,
    ):
        advance_tts_lifecycle(seg, state)
    assert get_tts_lifecycle(seg) == TTSLifecycleState.RELEASED
    with pytest.raises(TTSLifecycleError):
        advance_tts_lifecycle(seg, TTSLifecycleState.QUEUED)


def test_lifecycle_legacy_shortcuts_still_work():
    seg = {"segment_id": "y"}
    advance_tts_lifecycle(seg, TTSLifecycleState.QUEUED)
    advance_tts_lifecycle(seg, TTSLifecycleState.SYNTHESIZED)
    advance_tts_lifecycle(seg, TTSLifecycleState.VERIFIED)
    advance_tts_lifecycle(seg, TTSLifecycleState.SCHEDULED)
    advance_tts_lifecycle(seg, TTSLifecycleState.MERGED)
    advance_tts_lifecycle(seg, TTSLifecycleState.RELEASED)
    assert can_cleanup_wav(seg)


def test_ownership_and_cleanup_gate(tmp_path: Path):
    seg = _seg(tmp_path)
    advance_tts_lifecycle(seg, TTSLifecycleState.QUEUED)
    advance_tts_lifecycle(seg, TTSLifecycleState.SYNTHESIZED)
    advance_tts_lifecycle(seg, TTSLifecycleState.VERIFIED)
    advance_tts_lifecycle(seg, TTSLifecycleState.STORED)
    stamp_wav_owner(seg)
    assert stamp_wav_owner(seg) == WavOwner.TTS_ENGINE
    with pytest.raises(CleanupViolationError):
        assert_cleanup_allowed(seg)
    advance_tts_lifecycle(seg, TTSLifecycleState.SCHEDULED)
    advance_tts_lifecycle(seg, TTSLifecycleState.MERGED)
    stamp_wav_owner(seg)
    assert get_tts_lifecycle(seg) == TTSLifecycleState.MERGED
    with pytest.raises(CleanupViolationError):
        CleanupManager({"pipeline_state": "HANDOFF", "segments_data": [seg]}).try_unlink_segment_wav(
            seg, tmp_path / seg["file"]
        )


def test_registry_uuid_is_source_of_truth(tmp_path: Path):
    seg = _seg(tmp_path)
    reg = RuntimeRegistry(task_id="t1")
    rec = reg.upsert_from_segment(seg, path=tmp_path / seg["file"], actor="test")
    assert rec.segment_uuid == seg["segment_uuid"]
    assert Path(rec.path).is_file()
    assert reg.resolve_path(seg["segment_uuid"]) is not None
    out = reg.save(tmp_path / "reg.json")
    loaded = RuntimeRegistry.load(out)
    assert loaded.get(seg["segment_uuid"]).tts_uuid == seg["tts_uuid"]


def test_recovery_finds_wav_by_uuid(tmp_path: Path):
    seg = _seg(tmp_path, name="voice_abc.wav")
    ensure_all_uuids(seg)
    # Embed uuid token in filename
    real = tmp_path / f"tts_{seg['tts_uuid'][:12]}_x.wav"
    _wav(real)
    seg["file"] = "missing.wav"
    seg["tts_file_path"] = "missing.wav"
    info = {"task_id": "rec", "session_dir": str(tmp_path), "segments_data": [seg]}
    result = recover_missing_audio(seg, info)
    assert result.recovered
    assert Path(result.path).is_file()


def test_path_validation_corrupt_and_missing(tmp_path: Path):
    missing = validate_wav_path(tmp_path / "nope.wav")
    assert not missing.ok
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not-a-wav-file!!!!")
    corrupt = validate_wav_path(bad)
    assert not corrupt.ok


def test_validator_detects_missing_wav(tmp_path: Path):
    seg = _seg(tmp_path)
    seg["file"] = "gone.wav"
    seg["tts_file_path"] = "gone.wav"
    info = {
        "task_id": "v1",
        "translation_locked": False,
        "segments_data": [seg],
        "session_dir": str(tmp_path),
    }

    def resolve(ref, task_info=None):
        return tmp_path / Path(ref).name

    result = validate_runtime(
        info, stage="studio_handoff", require_tts=True, resolve_audio=resolve, attempt_recovery=False
    )
    assert not result.ok


def test_validator_recovery_then_pass(tmp_path: Path):
    seg = _seg(tmp_path)
    ensure_all_uuids(seg)
    real = tmp_path / f"tts_{seg['tts_uuid'][:12]}.wav"
    _wav(real)
    seg["file"] = "gone.wav"
    info = {
        "task_id": "v2",
        "translation_locked": False,
        "segments_data": [seg],
        "session_dir": str(tmp_path),
    }

    def resolve(ref, task_info=None):
        return tmp_path / Path(ref).name

    result = validate_runtime(
        info, stage="studio_handoff", require_tts=True, resolve_audio=resolve, attempt_recovery=True
    )
    assert result.ok
    assert result.recoveries


def test_diagnostic_zip_v2(tmp_path: Path):
    seg = _seg(tmp_path)
    info = {"task_id": "z", "pipeline_state": "HANDOFF", "segments_data": [seg]}
    result = validate_runtime(info, stage="studio_handoff", require_tts=False)
    zpath = write_diagnostic_zip(tmp_path, task_id="z", stage="handoff", result=result, info=info)
    assert zpath.is_file()
    import zipfile

    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
    assert "runtime_registry.json" in names
    assert "wav_index.json" in names
    assert "runtime_graph.json" in names


def test_handoff_blocks_on_integrity(tmp_path: Path):
    from engines.pipeline_integrity.runtime_validator import assert_studio_handoff_wavs

    seg = _seg(tmp_path)
    seg["file"] = "missing.wav"
    info = {"task_id": "h", "segments_data": [seg], "session_dir": str(tmp_path)}

    def resolve(ref, task_info=None):
        return tmp_path / Path(ref).name

    with pytest.raises((HandoffViolation, RuntimeIntegrityError)):
        assert_studio_handoff_wavs(info, resolve_audio=resolve)


def test_merge_integrity_empty_inputs(tmp_path: Path):
    seg = _seg(tmp_path)
    seg["file"] = "missing.wav"
    info = {"task_id": "m", "segments_data": [seg], "session_dir": str(tmp_path)}

    def resolve(ref, task_info=None):
        return tmp_path / Path(ref).name

    with pytest.raises(RuntimeIntegrityError):
        enforce_runtime(
            info,
            stage="merge",
            require_tts=True,
            require_merge=True,
            resolve_audio=resolve,
            attempt_recovery=False,
            output_dir=tmp_path,
        )


def test_long_run_synthetic_no_uuid_loss():
    """P3.1 §19 synthetic stress (scaled for CI)."""
    from engines.pipeline_integrity.uuid_chain import assert_uuids_unique, ensure_project_uuids

    rows = []
    for i in range(500):
        seg = {
            "segment_id": f"lr{i}",
            "translated_text": f"line-{i}",
            "start_ms": i * 1000,
            "end_ms": i * 1000 + 800,
        }
        ensure_all_uuids(seg)
        advance_tts_lifecycle(seg, TTSLifecycleState.QUEUED)
        advance_tts_lifecycle(seg, TTSLifecycleState.SYNTHESIZED)
        rows.append(seg)
    meta = ensure_project_uuids(rows)
    assert meta["segments"] == 500
    assert_uuids_unique(rows)
    uuids = [r["segment_uuid"] for r in rows]
    assert len(uuids) == len(set(uuids))
