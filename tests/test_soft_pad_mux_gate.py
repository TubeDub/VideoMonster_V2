# -*- coding: utf-8 -*-
"""TZ: missing audio → soft-pad; mux must NEVER return EXPORT_BLOCKED_MISSING_AUDIO."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_soft_pad_missing_segments_fills_hole(tmp_path):
    from api import auto_dub_api as api

    segs = [
        {
            "index": 0,
            "segment_id": "deadbeef0001",
            "text": "Кінець історії.",
            "final_tts_text": "Кінець історії.",
            "slot_ms": 2200,
            "start_ms": 10000,
            "end_ms": 12200,
            "needs_re_tts": True,
            "tts_ms": 0,
            "file": None,
        }
    ]
    info = {"session_dir": str(tmp_path)}
    stats = api._soft_pad_missing_segments(
        segs,
        task_info=info,
        task_id="softpad1",
        timing_map=[{"start": 10000, "end": 12200}],
    )
    assert stats["padded_count"] == 1
    assert 0 in stats["padded_indices"]
    assert segs[0]["audio_padded"] is True
    assert segs[0]["pad_reason"] == "missing_tts_after_repair"
    assert int(segs[0]["tts_ms"]) >= 200
    assert Path(segs[0]["file"]).is_file()
    assert Path(segs[0]["file"]).stat().st_size >= 1000
    assert info["final_status"] == "ok_with_pads"
    assert "export_blocked_reason" not in info
    assert info["padded_count"] == 1


def test_build_timed_dub_track_never_blocks_on_missing(tmp_path, monkeypatch):
    """Pre-mux gate must soft-pad and continue — no EXPORT_BLOCKED_MISSING_AUDIO."""
    from api import auto_dub_api as api
    from pydub import AudioSegment

    monkeypatch.setattr(api, "_artifacts_dir", lambda *_a, **_k: tmp_path)

    good = tmp_path / "ok.wav"
    AudioSegment.silent(duration=800).export(str(good), format="wav")

    segs = [
        {
            "index": 0,
            "segment_id": "okseg",
            "text": "Перший.",
            "final_tts_text": "Перший.",
            "file": str(good),
            "tts_ms": 800,
            "slot_ms": 1000,
            "start_ms": 0,
            "end_ms": 1000,
        },
        {
            "index": 1,
            "segment_id": "holeseg",
            "text": "Другий без аудіо.",
            "final_tts_text": "Другий без аудіо.",
            "file": None,
            "tts_ms": 0,
            "needs_re_tts": True,
            "slot_ms": 1500,
            "start_ms": 1000,
            "end_ms": 2500,
        },
    ]
    timing = [{"start": 0, "end": 1000}, {"start": 1000, "end": 2500}]
    task_info = {
        "session_dir": str(tmp_path),
        "voice": "mykyta",
        "tts_engine": "tts_uk",
        "target_duration_ms": 5000,
        "video_duration_ms": 5000,
    }

    # Fake AUTO_TASKS lookup
    fake_task = {"info": task_info}

    def _fail_regen(*_a, **_k):
        return (None, 0)

    with patch.object(api, "AUTO_TASKS", {"tsoft": fake_task}):
        with patch.object(api, "STATE_LOCK", MagicMock()):
            with patch.object(api, "_regen_segment_tts", side_effect=_fail_regen):
                with patch.object(api, "_commit_tts_group_result", return_value=None):
                    with patch.object(api, "_video_duration_ms", return_value=5000):
                        timed, warnings, report = api._build_timed_dub_track(
                            segs,
                            timing,
                            5000,
                            "tsoft",
                            style_params={},
                        )

    assert timed is not None, f"mux blocked: {warnings} {report}"
    assert "EXPORT_BLOCKED_MISSING_AUDIO" not in (warnings or [])
    assert "audio_missing_fatal" not in str(report)
    assert segs[1].get("audio_padded") or segs[1].get("silence_pad")
    assert int(segs[1].get("tts_ms") or 0) > 0
    assert task_info.get("final_status") in ("ok", "ok_with_pads")
    assert task_info.get("export_blocked_reason") in (None, "")
    assert int(task_info.get("padded_count") or 0) >= 1
    # Track length locked to video
    assert abs(len(timed) - 5000) <= 200


def test_tts_pipeline_reports_ok_with_pads_not_fatal(tmp_path):
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    wav = tmp_path / "pad_silence_x.wav"
    wav.write_bytes(b"z" * 1500)
    block = _build_openddf_tts_pipeline_block(
        {
            "session_dir": str(tmp_path),
            "padded_count": 1,
            "padded_indices": [1],
            "video_duration_ms": 5000,
            "track_duration_ms": 5000,
            "segments_data": [
                {
                    "index": 0,
                    "text": "a",
                    "final_tts_text": "a",
                    "file": str(wav),
                    "tts_ms": 800,
                },
                {
                    "index": 1,
                    "text": "b",
                    "final_tts_text": "b",
                    "file": str(wav),
                    "tts_ms": 1500,
                    "audio_padded": True,
                    "silence_pad": True,
                },
            ],
        }
    )
    assert block["final_status"] == "ok_with_pads"
    assert block["padded_count"] == 1
    assert 1 in block["padded_indices"]
    assert block["final_status"] != "audio_missing_fatal"
