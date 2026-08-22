# -*- coding: utf-8 -*-
"""Stage 40 — production-grade Simple EN→UK invariants (offline).

Locks:
1) missing TTS → soft_pad wav, census audio_missing==0, padded_count>0
2) forbidden cs-CZ voice → uk-UA-OstapNeural
3) overflow: text lever before atempo; atempo never >1.08 without text step
4) snapshot allows TTS identity-bind fields (diag 8c9850ef + bind extras)
5) cleanup keeps pad_silence_* / tts_* while keep_segment_audio
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _wav(path: Path, ms: int = 500, sr: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(ms / 1000.0 * sr))
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(b"\x00\x00" * frames)
    return path


def test_missing_tts_soft_pad_census_zero_missing(tmp_path, monkeypatch):
    import api.auto_dub_api as api
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    monkeypatch.setattr(
        api,
        "_repair_missing_tts_files",
        lambda *a, **k: {"repaired": 0, "padded": 0, "failed": 0},
    )

    session = tmp_path / "session"
    tid = "stage40pad"
    seg = {
        "segment_id": "hole0",
        "index": 0,
        "text": "Привіт",
        "final_tts_text": "Привіт",
        "start_ms": 0,
        "end_ms": 1200,
        "slot_ms": 1200,
        "tts_ms": 0,
        "file": None,
    }
    info = {
        "task_id": tid,
        "session_dir": str(session),
        "target_lang": "uk",
        "simple_pipeline": True,
        "happy_path": True,
    }
    out = api._prepare_segments_audio_before_mux(
        [seg],
        task_info=info,
        task_id=tid,
        timing_map=None,
        voice="uk-UA-OstapNeural",
    )
    assert out["ok"] is True
    pad = Path(seg["resolved_path"])
    assert pad.is_file()
    assert pad.stat().st_size >= 1000
    assert seg.get("audio_padded") is True
    assert int(info.get("padded_count") or 0) >= 1
    closed = session / "closed_loop" / tid / "pad_silence_hole0.wav"
    assert closed.is_file() or pad.name.startswith("pad_silence_") or pad.parent.name == "segs"
    block = _build_openddf_tts_pipeline_block(info, segments_data=[seg])
    assert block["audio_missing"] == 0, block
    assert block["audio_present"] == 1
    assert int(block.get("padded_count") or 0) >= 1
    assert block["final_status"] == "ok_with_pads"
    assert info["final_status"] == "ok_with_pads"
    assert info["final_status"] != "audio_missing_fatal"


def test_forbidden_cs_cz_voice_sanitizes_to_ostap():
    from engines.simple_voice_lock import DEFAULT_UK_VOICE, resolve_pipeline_voice

    v = resolve_pipeline_voice(
        {"target_lang": "uk", "voice": "cs-CZ-AntoninNeural"}
    )
    assert v == DEFAULT_UK_VOICE
    assert v.startswith("uk-UA-")
    from engines.simple_voice_lock import lock_simple_pipeline_voice

    segs = [{"text": "a", "voice": "sk-SK-LukasNeural"}]
    stamp = lock_simple_pipeline_voice(
        segs, pipeline_voice="cs-CZ-AntoninNeural", task_info={"target_lang": "uk"}
    )
    assert stamp["pipeline_voice"] == DEFAULT_UK_VOICE
    assert segs[0]["voice"] == DEFAULT_UK_VOICE


def test_overflow_text_before_atempo_never_above_108_without_text():
    from engines.text_slot_fit import (
        STAGE31_ATEMPO_MAX,
        STAGE31_SPEED_DELTA_MS,
        clamp_stage31_tempo,
        stage31_duration_levers,
    )

    levers = stage31_duration_levers(slot_ms=3000, tts_ms=5000)
    assert levers[0] == "text_slot_fit"
    assert "text_shorten" in levers
    assert "atempo" in levers
    assert levers.index("text_slot_fit") < levers.index("atempo")
    assert STAGE31_ATEMPO_MAX == 1.08
    assert STAGE31_SPEED_DELTA_MS == 150
    assert clamp_stage31_tempo(1.30) == 1.08
    assert clamp_stage31_tempo(0.70) == 0.92
    tiny = stage31_duration_levers(slot_ms=3000, tts_ms=3050)
    assert tiny == []


def test_snapshot_allows_tts_identity_bind_fields():
    from engines.pipeline_integrity.guards import StageSnapshotGuard
    from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage

    allowed = allowed_fields_for_stage("tts")
    for field in (
        "identity_binding",
        "tts_meta",
        "revision_text_hash",
        "wav_segment_id",
        "final_tts_text",
        "source_segment_uuid",
        "translation_uuid",
        "adaptation_uuid",
        "assigned_voice",
    ):
        assert field in allowed, field

    sid = "d67f009d933b4a29b629162ff4b23745"
    before = [
        {
            "segment_id": sid,
            "index": 0,
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
    after = [
        {
            "segment_id": sid,
            "index": 0,
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
            "tts_meta": {"segment_id": sid, "sidecar_path": r"C:\tmp\segs\0000.mp3.vm_rev.json"},
            "revision_text_hash": "hashhashhashhashhashhash",
            "wav_segment_id": sid,
            "file": r"C:\tmp\segs\0000.mp3",
            "tts_file_path": r"C:\tmp\segs\0000.mp3",
        }
    ]
    StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")
    assert StageSnapshotGuard.diff_violations(before, after, stage="tts") == []


def test_duration_control_new_write_includes_text_slot_fit():
    from engines.text_slot_fit import (
        canon_duration_control_used,
        merge_text_slot_fit_stamp,
    )

    used = merge_text_slot_fit_stamp(None, "text_shorten")
    parts = used.split("+")
    assert "text_slot_fit" in parts
    assert "text_shorten" in parts
    # Aliases stay themselves so Stage 23/31 reads still match.
    assert canon_duration_control_used("text_shorten") == "text_shorten"
    assert canon_duration_control_used("text_expand") == "text_expand"
    assert canon_duration_control_used("text_slot_fit+atempo") == "text_slot_fit+atempo"


def test_simple_snapshot_mismatch_continues(tmp_path):
    import copy

    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task
    from engines.pipeline_integrity.guards import PipelineIntegrityCoordinator
    from engines.pipeline_integrity.segment import new_segment_id

    task_id = "stage40snap"
    init_auto_task(
        task_id,
        {
            "status": "running",
            "step": "tts",
            "info": {
                "simple_pipeline": True,
                "happy_path": True,
                "session_dir": str(tmp_path / "sess"),
            },
        },
    )
    from engines.dubbing_engine.project_session import ProjectSession

    session = ProjectSession("sess40", tmp_path, task_id=task_id)
    sid = new_segment_id()
    rows = [{"segment_id": sid, "index": 0, "text": "ok", "plain_text": "ok"}]
    coord = PipelineIntegrityCoordinator(task_id=task_id)
    coord.assign_segment_ids(rows)
    assert coord.initialize_guard_context(project_session=session, segments_data=rows)
    coord.begin_stage("tts", rows)
    after = copy.deepcopy(rows)
    after[0]["text"] = "mutated translation"
    coord.end_stage("tts", after)
    assert any(r.get("snapshot_guard") == "soft_continue" for r in coord.reports)
    with STATE_LOCK:
        info = AUTO_TASKS[task_id]["info"]
        assert info.get("snapshot_soft_continue") is True


def test_cleanup_keeps_pad_and_tts_while_keep_segment_audio(tmp_path):
    from engines.pipeline_cleanup import cleanup_intermediate_work_dirs
    from engines.pipeline_integrity.cleanup_manager import CleanupManager

    session = tmp_path / "sess"
    session.mkdir()
    pad = _wav(session / "pad_silence_x.wav", ms=400)
    tts = _wav(session / "tts_0000.wav", ms=400)
    fitted = _wav(session / "fitted_0000.wav", ms=400)
    junk = session / "temp_extract.wav"
    junk.write_bytes(b"RIFF" + b"\x00" * 8)

    cleanup_intermediate_work_dirs(session, keep_segment_audio=True)
    assert pad.is_file()
    assert tts.is_file()
    assert fitted.is_file()

    info = {"pipeline_state": "SPEECH_READY", "session_dir": str(session)}
    CleanupManager(info).cleanup_session(
        session, success=False, mux_inputs_live=True
    )
    assert pad.is_file()
    assert tts.is_file()
    assert fitted.is_file()


def test_pre_mux_integrity_simple_does_not_raise_on_forbidden_voice():
    from engines.simple_voice_lock import DEFAULT_UK_VOICE
    from engines.tts_lang_lock import pre_mux_tts_integrity

    segs = [
        {
            "assigned_voice": "cs-CZ-AntoninNeural",
            "voice": "cs-CZ-AntoninNeural",
            "final_tts_text": "Привіт друзі сьогодні українською мовою говоримо.",
            "tts_duration": 1.0,
        }
    ]
    out = pre_mux_tts_integrity(segs, target_lang="uk", simple_mode=True)
    assert segs[0]["voice"] == DEFAULT_UK_VOICE
    assert out.get("rerouted_default_uk") is True
