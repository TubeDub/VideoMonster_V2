"""HotFix №1 — TTS handoff from session dir to track builder."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def session_tts_setup(tmp_path, monkeypatch):
    """TTS file only in output/sessions/<task_id>/, not flat output/."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))

    task_id = uuid.uuid4().hex
    session_dir = output_dir / "sessions" / task_id
    session_dir.mkdir(parents=True)
    mp3 = session_dir / "segment_0001.mp3"
    mp3.write_bytes(b"\xff\xfb" + b"\x00" * 64)

    task_info = {
        "session_dir": str(session_dir),
        "tts_files": [mp3.name],
        "mux_base_id": task_id[:8],
    }

    studio_seg = {
        "id": "0",
        "index": 0,
        "text": "Hello",
        "start_ms": 0,
        "end_ms": 2000,
        "file": mp3.name,
    }
    state = {
        "session_id": task_id,
        "task_id": task_id,
        "segments": [studio_seg],
        "timing_map": [{"start": 0, "end": 2000}],
        "duration_ms": 5000,
    }
    return {
        "task_id": task_id,
        "mp3": mp3,
        "task_info": task_info,
        "state": state,
        "output_dir": output_dir,
    }


def test_segment_audio_name_resolves_session_dir(session_tts_setup, monkeypatch):
    from api import studio_api

    monkeypatch.setattr(
        studio_api,
        "_task_info_for",
        lambda tid: session_tts_setup["task_info"] if tid == session_tts_setup["task_id"] else {},
    )
    seg = session_tts_setup["state"]["segments"][0]
    name = studio_api._segment_audio_name(seg, task_id=session_tts_setup["task_id"])
    assert name == session_tts_setup["mp3"].name


def test_segments_data_from_state_keeps_files_in_session_dir(session_tts_setup, monkeypatch):
    from api import studio_api

    monkeypatch.setattr(
        studio_api,
        "_task_info_for",
        lambda tid: session_tts_setup["task_info"] if tid == session_tts_setup["task_id"] else {},
    )
    segments_data, _timing = studio_api._segments_data_from_state(session_tts_setup["state"])
    assert len(segments_data) == 1
    assert segments_data[0]["file"] == session_tts_setup["mp3"].name


def test_build_timed_dub_track_finds_session_mp3(session_tts_setup, monkeypatch):
    """Track builder must resolve TTS via task_info.session_dir, not flat output/."""
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task
    from api import auto_dub_api

    task_id = session_tts_setup["task_id"]
    init_auto_task(
        task_id,
        {
            "status": "studio_ready",
            "info": session_tts_setup["task_info"],
        },
    )

    segments_data = [
        {
            "index": 0,
            "text": "Hello",
            "file": session_tts_setup["mp3"].name,
        }
    ]
    timing_map = [{"start": 0, "end": 2000}]

    fake_audio = object()

    def _fake_build_gap(*_args, **_kwargs):
        return fake_audio, [], {"ok": True}

    monkeypatch.setattr(
        auto_dub_api,
        "_build_gap_adjusted_track_no_double_soft_sync",
        _fake_build_gap,
    )
    monkeypatch.setattr(
        auto_dub_api,
        "_premux_segment_fits",
        lambda path, slot_ms: (True, 500),
    )
    monkeypatch.setattr(
        auto_dub_api,
        "AudioSegment",
        type(
            "AS",
            (),
            {
                "from_file": staticmethod(lambda _p: type("Seg", (), {"__len__": lambda s: 500})()),
            },
        ),
    )
    monkeypatch.setattr(auto_dub_api, "_write_dub_segment_log", lambda *a, **k: None)

    timed, warnings, report = auto_dub_api._build_timed_dub_track(
        segments_data,
        timing_map,
        5000,
        task_id,
    )
    assert timed is fake_audio
    assert warnings == []


def test_flat_output_legacy_still_works(tmp_path, monkeypatch):
    """Pre–Stage 2 projects with MP3 in flat output/ continue to work."""
    from api import studio_api

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(studio_api, "OUTPUT_DIR", output_dir)

    mp3 = output_dir / "legacy_seg.mp3"
    mp3.write_bytes(b"\xff\xfb" + b"\x00" * 32)

    seg = {"file": mp3.name}
    name = studio_api._segment_audio_name(seg, task_id="no-session")
    assert name == mp3.name


def test_intermediate_cleanup_preserves_session_tts_audio(tmp_path):
    """RCA: `for pattern in ("*.json")` iterated chars; the '*' glob deleted

    every file (incl. TTS mp3) before the studio mix → "Нет TTS-файлов".
    Cleanup must keep segment audio in the session root / salvage from work dirs.
    """
    from engines.pipeline_cleanup import cleanup_intermediate_work_dirs

    session = tmp_path / "sessions" / "task1"
    session.mkdir(parents=True)
    (session / "e713d101_g0000.mp3").write_bytes(b"\xff\xfb\x00")
    (session / "segment_0001.mp3").write_bytes(b"\xff\xfb\x00")
    (session / "tts_0000.mp3").write_bytes(b"\xff\xfb" + b"\x00" * 1200)
    (session / "project.json").write_text("{}")
    (session / "intermediate.json").write_text("{}")
    (session / "slot_fit").mkdir()
    (session / "slot_fit" / "slot_fit_000.wav").write_bytes(b"R" * 1500)
    (session / "slot_fit" / "cand.tmp.wav").write_bytes(b"\xff")

    cleanup_intermediate_work_dirs(session, keep_segment_audio=True)

    assert (session / "e713d101_g0000.mp3").is_file()
    assert (session / "segment_0001.mp3").is_file()
    assert (session / "tts_0000.mp3").is_file()
    assert (session / "project.json").is_file()
    assert not (session / "intermediate.json").exists()
    # Protected audio salvaged from work subdir to session root.
    assert (session / "slot_fit_000.wav").is_file()
    assert (session / "slot_fit_000.wav").stat().st_size >= 1000


def test_empty_tts_diagnosis_returns_reason():
    """TZ §8: empty-TTS diagnosis must return a structured reason, not silence."""
    from engines.dubbing_engine.tts_handoff_diag import log_empty_tts_diagnosis

    report = log_empty_tts_diagnosis(
        "taskX",
        task_info={"session_dir": "output/sessions/taskX", "tts_files": ["a.mp3"]},
        segments_data=[{"index": 0, "text": "Привіт", "file": None}],
        segment_paths=[],
        stage="test",
    )
    assert report["reason"]
    assert report["segments_translated"] == 1
    assert report["segments_with_file"] == 0
    assert "search_paths" in report
