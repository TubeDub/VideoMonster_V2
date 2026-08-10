# -*- coding: utf-8 -*-
"""TZ: dubbing track must reach video end; holes get re-TTS or silence pad."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_segment_needs_repair_on_tts_ms_zero_and_needs_re_tts(tmp_path):
    from engines.pipeline_integrity.audio_presence import segment_needs_audio_repair

    good = tmp_path / "ok.wav"
    good.write_bytes(b"x" * 1500)

    assert (
        segment_needs_audio_repair(
            {
                "text": "Привіт",
                "final_tts_text": "Привіт",
                "file": str(good),
                "tts_ms": 0,
            }
        )
        is True
    )
    assert (
        segment_needs_audio_repair(
            {
                "text": "Привіт",
                "final_tts_text": "Привіт",
                "file": str(good),
                "tts_ms": 1200,
                "needs_re_tts": True,
            }
        )
        is True
    )
    assert (
        segment_needs_audio_repair(
            {
                "text": "Привіт",
                "final_tts_text": "Привіт",
                "file": str(good),
                "tts_ms": 1200,
            }
        )
        is False
    )


def test_repair_silence_pad_after_tts_fail(tmp_path, monkeypatch):
    from api import auto_dub_api as api

    monkeypatch.setattr(api, "_artifacts_dir", lambda *_a, **_k: tmp_path)
    segs = [
        {
            "index": 0,
            "segment_id": "child1",
            "text": "Фінал історії.",
            "final_tts_text": "Фінал історії.",
            "slot_ms": 2500,
            "needs_re_tts": True,
            "post_restore_split": True,
            "tts_ms": 0,
        }
    ]

    def _fail_regen(*_a, **_k):
        return (None, 0)

    with patch.object(api, "_regen_segment_tts", side_effect=_fail_regen):
        stats = api._repair_missing_tts_files(
            segs,
            voice="mykyta",
            task_info={"tts_engine": "tts_uk"},
            task_id="tpad",
        )
    assert stats["padded"] == 1
    assert segs[0]["silence_pad"] is True
    assert int(segs[0]["tts_ms"]) > 0
    assert Path(segs[0]["file"]).is_file()
    assert Path(segs[0]["file"]).stat().st_size >= 1000


def test_repair_missing_tts_writes_real_file(tmp_path, monkeypatch):
    from api import auto_dub_api as api

    monkeypatch.setattr(api, "_artifacts_dir", lambda *_a, **_k: tmp_path)
    out = tmp_path / "repaired.wav"

    def _fake_regen(*_a, **_k):
        out.write_bytes(b"RIFF" + b"\x00" * 1500)
        return (str(out), 1200)

    segs = [
        {
            "index": 0,
            "segment_id": "aaa",
            "text": "Привіт світе.",
            "final_tts_text": "Привіт світе.",
            "status": "pending_regen",
            "tts_status": "pending_regen",
            "needs_re_tts": True,
        }
    ]
    with patch.object(api, "_regen_segment_tts", side_effect=_fake_regen):
        with patch.object(api, "_commit_tts_group_result", return_value=None):
            stats = api._repair_missing_tts_files(
                segs,
                voice="mykyta",
                task_info={"tts_engine": "tts_uk"},
                task_id="t1",
            )
    assert stats["repaired"] == 1
    assert segs[0]["tts_ms"] == 1200
    assert segs[0]["needs_re_tts"] is False


def test_build_gap_adjusted_track_pads_to_video():
    from pydub import AudioSegment
    from engines.timing_fit import build_gap_adjusted_track

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        seg = base / "a.wav"
        AudioSegment.silent(duration=800).export(str(seg), format="wav")
        master, _logs, report = build_gap_adjusted_track(
            [str(seg)],
            [{"start": 0, "end": 1000}],
            video_duration_ms=5000,
        )
        assert abs(len(master) - 5000) <= 50
        assert int(report.get("video_duration_ms") or 0) == 5000
        assert int(report.get("track_duration_ms") or 0) == len(master)
        assert int(report.get("tail_gap_ms") or 0) >= 3000


def test_final_dub_qa_track_shorter_warning():
    from engines.segment_timing_qa import build_final_dub_qa_report

    report = build_final_dub_qa_report(
        {
            "source_segments": ["Hello."],
            "segments_data": [
                {
                    "index": 0,
                    "text": "Привіт.",
                    "plain_text": "Привіт.",
                    "playback_duration": 900,
                    "tts_ms": 900,
                    "file": "seg0.mp3",
                }
            ],
            "translation_audits": [
                {
                    "index": 0,
                    "raw_translation": "Привіт.",
                    "final_text": "Привіт.",
                    "tts_text": "Привіт.",
                }
            ],
            "timing_map": [{"start": 0, "end": 2000}],
            "target_lang": "uk",
            "detected_lang": "en",
            "video_duration_ms": 10000,
            "track_duration_ms": 9000,
        }
    )
    assert report.get("video_duration_ms") == 10000
    assert report.get("track_duration_ms") == 9000
    assert report.get("tail_gap_ms") == 1000
    assert report.get("final_status") == "track_shorter_than_video"


def test_tts_pipeline_block_has_duration_fields():
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        wav.write_bytes(b"z" * 1500)
        block = _build_openddf_tts_pipeline_block(
            {
                "session_dir": td,
                "segments_data": [
                    {
                        "index": 0,
                        "text": "x",
                        "final_tts_text": "x",
                        "file": str(wav),
                        "tts_ms": 1000,
                    }
                ],
                "video_duration_ms": 8000,
                "track_duration_ms": 8000,
            }
        )
    assert block["video_duration_ms"] == 8000
    assert block["track_duration_ms"] == 8000
    assert block["tail_gap_ms"] == 0
    assert block["audio_missing"] == 0
