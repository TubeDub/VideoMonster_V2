"""Tests — PIPELINE_AUDIO_IDENTITY / unique TTS filenames."""

from __future__ import annotations

from pathlib import Path

from engines.pipeline_integrity.audio_identity import (
    allocate_tts_path,
    bind_segment_audio,
    ensure_segment_uuid,
    ensure_unique_before_handoff,
    find_duplicate_filenames,
    repair_duplicate_tts_filenames,
    unique_tts_basename,
    validate_audio_identity,
)
from engines.pipeline_integrity.segment import new_segment_id


def test_unique_tts_basename_includes_uuid_not_index_only():
    name = unique_tts_basename(segment_uuid="b5c43e72a6d7", run_id="ed076c12", ext=".wav")
    assert "b5c43e72a6d7" in name
    assert name.startswith("tts_")
    assert name.endswith(".wav")
    assert "seg0000" not in name


def test_allocate_tts_path_never_collides(tmp_path):
    a = allocate_tts_path(tmp_path, segment_uuid="aaa", purpose="tts")
    a.write_bytes(b"x")
    b = allocate_tts_path(tmp_path, segment_uuid="aaa", purpose="tts")
    assert a.name != b.name
    assert not b.exists()


def test_ensure_segment_uuid_syncs_with_segment_id():
    seg = {"segment_id": "abc123"}
    assert ensure_segment_uuid(seg) == "abc123"
    assert seg["segment_uuid"] == "abc123"


def test_find_and_repair_duplicate_filenames(tmp_path):
    shared = tmp_path / "shared.wav"
    shared.write_bytes(b"RIFF" + b"\x00" * 100)
    sid_a = new_segment_id()
    sid_b = new_segment_id()
    segs = [
        {"segment_id": sid_a, "index": 0, "file": "shared.wav", "tts_file_path": "shared.wav"},
        {"segment_id": sid_b, "index": 1, "file": "shared.wav", "tts_file_path": "shared.wav"},
    ]
    dupes = find_duplicate_filenames(segs)
    assert "shared.wav" in dupes
    assert len(dupes["shared.wav"]) == 2

    repairs = repair_duplicate_tts_filenames(
        segs,
        resolve_path=lambda p: tmp_path / Path(p).name,
        dest_dir=tmp_path,
        run_id="run1",
    )
    assert repairs
    assert repairs[0]["status"] == "repaired"
    names = {segs[0]["file"], segs[1]["file"]}
    assert len(names) == 2
    assert find_duplicate_filenames(segs) == {}


def test_validate_audio_identity_ok_after_unique_bind(tmp_path):
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    segs = [
        {"segment_id": new_segment_id(), "index": 0},
        {"segment_id": new_segment_id(), "index": 1},
    ]
    bind_segment_audio(segs[0], a, duration_ms=100)
    bind_segment_audio(segs[1], b, duration_ms=200)
    result = validate_audio_identity(segs)
    assert result["ok"] is True
    assert result["duplicate_filenames"] is False


def test_ensure_unique_before_handoff_writes_reports(tmp_path):
    shared = tmp_path / "dup.wav"
    shared.write_bytes(b"RIFF" + b"\x00" * 64)
    segs = [
        {
            "segment_id": new_segment_id(),
            "index": 0,
            "file": "dup.wav",
            "tts_file_path": "dup.wav",
            "playback_duration": 100,
        },
        {
            "segment_id": new_segment_id(),
            "index": 1,
            "file": "dup.wav",
            "tts_file_path": "dup.wav",
            "playback_duration": 120,
        },
    ]
    app_dir = tmp_path / "app"
    (app_dir / "output").mkdir(parents=True)
    result = ensure_unique_before_handoff(
        segs,
        resolve_path=lambda p: tmp_path / Path(p).name,
        dest_dir=tmp_path,
        run_id="taskabc",
        app_dir=app_dir,
    )
    assert result["ok"] is True
    assert result["repairs"]
    reg = app_dir / "output" / "diagnostics" / "taskabc" / "audio_registry.json"
    ident = app_dir / "output" / "diagnostics" / "taskabc" / "audio_identity_report.json"
    assert reg.is_file()
    assert ident.is_file()


def test_pipeline_validator_passes_after_repair(tmp_path):
    from engines.pipeline_integrity.guards import PipelineValidator

    shared = tmp_path / "x.wav"
    shared.write_bytes(b"RIFF" + b"\x00" * 64)
    segs = [
        {
            "segment_id": new_segment_id(),
            "index": 0,
            "file": "x.wav",
            "start_ms": 0,
            "end_ms": 1000,
        },
        {
            "segment_id": new_segment_id(),
            "index": 1,
            "file": "x.wav",
            "start_ms": 1000,
            "end_ms": 2000,
        },
    ]
    timing = [{"start": 0, "end": 1000}, {"start": 1000, "end": 2000}]
    ensure_unique_before_handoff(
        segs,
        resolve_path=lambda p: tmp_path / Path(p).name,
        dest_dir=tmp_path,
        run_id="r1",
    )
    # After repair, basenames must be unique for validator
    out = PipelineValidator.validate(
        segs,
        timing,
        stage="studio_handoff",
        resolve_audio=lambda p, task_info=None: tmp_path / Path(str(p)).name,
    )
    assert out["with_tts"] == 2
